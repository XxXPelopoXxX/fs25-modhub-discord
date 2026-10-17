import os
import json
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

SOURCE = "https://www.farming-simulator.com/mods.php?filter=latest"
STATE = Path("seen.json")
WEBHOOK = os.environ["DISCORD_WEBHOOK"]

# Opcional: URL directa del logo cuadrado de FS25.
# Si no se configura, intentaremos encontrarlo en la web oficial.
FS25_LOGO_URL = os.getenv("FS25_LOGO_URL", "").strip()

GREEN = 0x76B900
MAX_NEW_MODS = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; FS25ModHubMonitor/3.0)"
}

session = requests.Session()
session.headers.update(HEADERS)


def get_soup(url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def absolute_url(url, base):
    if not url:
        return None
    return urljoin(base, url.strip())


def find_mod_image(soup, base_url):
    # Preferir la imagen oficial de la ficha del mod.
    for attrs in (
        {"property": "og:image"},
        {"name": "twitter:image"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            return absolute_url(tag["content"], base_url)

    # Como alternativa, buscar imágenes de la propia ficha.
    for img in soup.find_all("img", src=True):
        src = img.get("src", "")
        alt = img.get("alt", "").lower()

        if any(word in alt for word in ("preview", "mod", "vehicle")):
            return absolute_url(src, base_url)

    return None


def find_fs25_logo(soup):
    if FS25_LOGO_URL:
        return FS25_LOGO_URL

    # Intentar detectar un logo del juego en la página oficial.
    for img in soup.find_all("img", src=True):
        src = img.get("src", "")
        description = " ".join([
            src,
            img.get("alt", ""),
            img.get("title", ""),
        ]).lower()

        if (
            ("fs25" in description or "farming-simulator-25" in description)
            and ("logo" in description or "icon" in description)
        ):
            return absolute_url(src, SOURCE)

    return None


def get_mods():
    soup = get_soup(SOURCE)
    mods = {}

    for link in soup.find_all("a", href=True):
        url = absolute_url(link["href"], SOURCE)
        parsed = urlparse(url)

        if not parsed.path.endswith("/mod.php"):
            continue

        mod_id = parse_qs(parsed.query).get("mod_id", [None])[0]
        if not mod_id:
            continue

        card = link
        for _ in range(8):
            if card.parent is None:
                break
            card = card.parent
            if card.find(["h2", "h3", "h4"]):
                break

        heading = card.find(["h2", "h3", "h4"])
        if not heading:
            continue

        name = heading.get_text(" ", strip=True)
        if not name:
            continue

        image_url = None
        img = card.find("img", src=True)
        if img:
            image_url = absolute_url(img["src"], SOURCE)

        mods[str(mod_id)] = {
            "name": name,
            "url": url,
            "image": image_url,
        }

    if not mods:
        raise RuntimeError(
            "No se detectaron mods en el ModHub. "
            "Puede haber cambiado el HTML de la página."
        )

    return mods, find_fs25_logo(soup)


def extract_details(mod):
    details = {
        "game": "Farming Simulator 25",
        "manufacturer": None,
        "category": None,
        "author": None,
        "size": None,
        "version": None,
        "released": None,
        "platform": None,
        "image": mod.get("image"),
    }

    soup = get_soup(mod["url"])

    details["image"] = find_mod_image(soup, mod["url"]) or details["image"]

    # Recoger texto visible para buscar las etiquetas de la ficha.
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in soup.get_text("\n").splitlines()
    ]
    lines = [line for line in lines if line]

    aliases = {
        "manufacturer": ("Manufacturer", "Brand"),
        "category": ("Category",),
        "author": ("Author", "Modder"),
        "size": ("Size", "File size"),
        "version": ("Version",),
        "released": ("Released", "Release date", "Date"),
        "platform": ("Platform", "Platforms"),
    }

    # Algunas fichas presentan la etiqueta y el valor en líneas separadas;
    # otras los muestran juntos, por ejemplo "Author: Nombre".
    for field, labels in aliases.items():
        for index, line in enumerate(lines):
            matched_label = next(
                (
                    label for label in labels
                    if line.lower().startswith(label.lower() + ":")
                ),
                None,
            )

            if matched_label:
                value = line.split(":", 1)[1].strip()
                if value:
                    details[field] = value
                    break

            if line.rstrip(":").lower() in {
                label.lower() for label in labels
            }:
                if index + 1 < len(lines):
                    candidate = lines[index + 1].strip()
                    if candidate and len(candidate) < 300:
                        details[field] = candidate
                        break

    return details


def add_field(embed, label, value):
    if value:
        embed["fields"].append({
            "name": label,
            "value": str(value)[:1024],
            "inline": False,
        })


def send_discord_message(mod, logo_url):
    details = extract_details(mod)

    # La tarjeta principal reproduce el estilo de tu captura.
    embed = {
        "author": {
            "name": "ModHub - New Mod!",
            **({"icon_url": logo_url} if logo_url else {}),
        },
        "title": details.get("name") or mod["name"],
        "url": mod["url"],
        "color": GREEN,
        "fields": [],
        "footer": {
            "text": "Farming Simulator 25 • ModHub oficial",
        },
    }

    add_field(embed, "Game", details["game"])
    add_field(embed, "Manufacturer", details["manufacturer"])
    add_field(embed, "Category", details["category"])
    add_field(embed, "Author", details["author"])
    add_field(embed, "Size", details["size"])
    add_field(embed, "Version", details["version"])
    add_field(embed, "Released", details["released"])
    add_field(embed, "Platform", details["platform"])

    # Imagen pequeña arriba a la derecha.
    if logo_url:
        embed["thumbnail"] = {"url": logo_url}

    # Imagen grande del mod debajo de los campos.
    if details["image"]:
        embed["image"] = {"url": details["image"]}

    # Enlace clicable equivalente a "View Mod".
    embed["fields"].append({
        "name": "Link",
        "value": f"[View Mod]({mod['url']})",
        "inline": False,
    })

    payload = {
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }

    response = session.post(
        WEBHOOK,
        json=payload,
        timeout=20,
    )
    response.raise_for_status()


def save_state(seen):
    STATE.write_text(
        json.dumps(seen, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def main():
    mods, logo_url = get_mods()

    if not STATE.exists():
        save_state(mods)
        print(f"Configuración inicial: {len(mods)} mods registrados.")
        return

    with STATE.open("r", encoding="utf-8") as file:
        seen = json.load(file)

    if isinstance(seen, list):
        seen = {str(item): {} for item in seen}

    new_ids = [mod_id for mod_id in mods if mod_id not in seen]

    if not new_ids:
        print("No se han detectado mods nuevos.")

    for mod_id in new_ids[:MAX_NEW_MODS]:
        mod = mods[mod_id]

        try:
            send_discord_message(mod, logo_url)
        except (requests.RequestException, ValueError) as error:
            print(f"Error al publicar {mod['name']}: {error}")
            break

        seen[mod_id] = mod
        save_state(seen)
        print(f"Publicado: {mod['name']}")

    # Actualizar los datos conocidos sin volver a anunciar los mods.
    for mod_id, mod in mods.items():
        if mod_id in seen:
            previous = seen[mod_id]
            if not isinstance(previous, dict):
                previous = {}
            seen[mod_id] = {**previous, **mod}

    save_state(seen)


if __name__ == "__main__":
    main()
