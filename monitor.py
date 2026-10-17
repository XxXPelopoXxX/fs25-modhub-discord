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

# Configura FS25_LOGO_URL como variable de entorno/secreto de GitHub
# con una URL pública directa a un PNG/JPG cuadrado del logo de FS25.
FS25_LOGO_URL = os.getenv("FS25_LOGO_URL", "").strip()

GREEN = 0x76B900
MAX_NEW_MODS = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; FS25ModHubMonitor/3.1)"
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
    for attrs in (
        {"property": "og:image"},
        {"name": "twitter:image"},
    ):
        tag = soup.find("meta", attrs=attrs)
        if tag and tag.get("content"):
            return absolute_url(tag["content"], base_url)

    for img in soup.find_all("img", src=True):
        description = " ".join([
            img.get("src", ""),
            img.get("alt", ""),
            img.get("title", ""),
        ]).lower()

        if any(word in description for word in ("preview", "vehicle", "mod")):
            return absolute_url(img["src"], base_url)

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

        img = card.find("img", src=True)
        image_url = absolute_url(img["src"], SOURCE) if img else None

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

    return mods


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

    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in soup.get_text("\n").splitlines()
    ]
    lines = [line for line in lines if line]

    aliases = {
        "manufacturer": ("Manufacturer", "Brand", "Hersteller"),
        "category": ("Category", "Kategorie"),
        "author": ("Author", "Modder", "Autor"),
        "size": ("Size", "File size", "Größe"),
        "version": ("Version",),
        "released": ("Released", "Release date", "Date", "Veröffentlicht"),
        "platform": ("Platform", "Platforms", "Plattform"),
    }

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


def send_discord_message(mod):
    details = extract_details(mod)

    embed = {
        "author": {
            "name": "🟩 ModHub - New Mod!",
        },
        "title": details.get("name") or mod["name"],
        "url": mod["url"],
        "color": GREEN,
        "description": (
            f"**Game:** {details['game']}\n"
            f"**Manufacturer:** {details['manufacturer'] or 'N/A'}\n"
            f"**Category:** {details['category'] or 'N/A'}\n"
            f"**Author:** {details['author'] or 'N/A'}\n"
            f"**Size:** {details['size'] or 'N/A'}\n"
            f"**Version:** {details['version'] or 'N/A'}\n"
            f"**Released:** {details['released'] or 'N/A'}\n"
            f"**Platform:** {details['platform'] or 'N/A'}"
        ),
        "fields": [
            {
                "name": "🔗 Link",
                "value": f"[View Mod]({mod['url']})",
                "inline": True,
            }
        ],
        "footer": {
            "text": "Farming Simulator 25 • ModHub",
        },
    }

    # Logo pequeño arriba a la derecha. La URL debe ser pública y directa.
    if FS25_LOGO_URL:
        embed["thumbnail"] = {"url": FS25_LOGO_URL}

    # No usar embed["image"]: eso genera la imagen grande debajo.
    # Se conserva solo la miniatura del logo para que el aviso sea compacto.

    response = session.post(
        WEBHOOK,
        json={
            "embeds": [embed],
            "allowed_mentions": {"parse": []},
        },
        timeout=20,
    )
    response.raise_for_status()


def save_state(seen):
    STATE.write_text(
        json.dumps(seen, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def main():
    mods = get_mods()

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
            send_discord_message(mod)
        except (requests.RequestException, ValueError) as error:
            print(f"Error al publicar {mod['name']}: {error}")
            break

        seen[mod_id] = mod
        save_state(seen)
        print(f"Publicado: {mod['name']}")

    for mod_id, mod in mods.items():
        if mod_id in seen:
            previous = seen[mod_id]
            if not isinstance(previous, dict):
                previous = {}
            seen[mod_id] = {**previous, **mod}

    save_state(seen)


if __name__ == "__main__":
    main()
