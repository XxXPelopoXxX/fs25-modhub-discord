import os
import json
import re
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

SOURCE = "https://www.farming-simulator.com/mods.php?filter=latest"
STATE = Path("seen.json")
WEBHOOK = os.environ["DISCORD_WEBHOOK"]

FS_GREEN = 0x76B900
MAX_NEW_MODS = 10

# URL del icono verde "25" que aparece arriba a la derecha como Thumbnail
ICON_25_URL = "https://cdn.farming-simulator.com/favicon.ico" # O la URL directa del logo verde de FS25

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; FS25ModHubMonitor/2.0)"
}

session = requests.Session()
session.headers.update(HEADERS)


def get_soup(url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def get_mods():
    soup = get_soup(SOURCE)
    mods = {}

    for link in soup.find_all("a", href=True):
        url = urljoin(SOURCE, link["href"])
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
        image = card.find("img", src=True)
        if image:
            image_url = urljoin(SOURCE, image["src"])

        mods[str(mod_id)] = {
            "name": name,
            "url": url,
            "image": image_url,
        }

    if not mods:
        raise RuntimeError(
            "No se detectaron mods. Revisa el HTML del ModHub."
        )

    return mods


def get_next_value(lines, label):
    """Busca el valor que aparece después de una etiqueta."""
    for index, line in enumerate(lines):
        if line.strip().rstrip(":").lower() == label.lower():
            for following in lines[index + 1:]:
                following = following.strip()
                if following:
                    return following
    return None


def get_mod_details(mod):
    """Obtiene los atributos detallados y la imagen real del mod evitando la genérica."""
    details = {
        "author": "N/A",
        "category": "N/A",
        "platform": "PC/MAC, PS5, XBS, S2",
        "manufacturer": None,
        "size": None,
        "version": "1.0.0.0",
        "released": None,
        "game": "Farming Simulator 25",
        "image": mod.get("image"),
    }

    try:
        soup = get_soup(mod["url"])

        # Intentar buscar la imagen real del mod dentro de la galería o contenedor de imágenes de la ficha
        mod_image = None
        
        # En el ModHub, la imagen grande del mod suele estar en un contenedor específico (ej. etiqueta img con clase o dentro de un visor)
        # Buscamos primero imágenes dentro del contenido principal del mod que no sean banners genéricos
        for img in soup.find_all("img", src=True):
            src = img["src"]
            # Filtramos para buscar imágenes que pertenezcan al CDN de mods y no sean iconos o logos genéricos
            if "modhub" in src or "dlc" in src or "storage" in src:
                if "default" not in src and "share" not in src and "logo" not in src:
                    mod_image = urljoin(mod["url"], src)
                    break

        # Si no encontramos una específica en el cuerpo, probamos con las metatags pero validando que no sea la genérica
        if not mod_image:
            for attribute in (
                {"property": "og:image"},
                {"name": "twitter:image"},
            ):
                meta = soup.find("meta", attrs=attribute)
                if meta and meta.get("content"):
                    content_url = meta["content"]
                    # Si la meta imagen no es la genérica de farming-simulator.com por defecto
                    if "default" not in content_url and "sharing" not in content_url:
                        mod_image = urljoin(mod["url"], content_url)
                        break

        # Si se encontró una imagen válida del mod, la actualizamos
        if mod_image:
            details["image"] = mod_image

        # Extraer los textos de las etiquetas de la ficha
        lines = [
            line.strip()
            for line in soup.get_text("\n").splitlines()
            if line.strip()
        ]

        author = get_next_value(lines, "Author")
        if author:
            details["author"] = author

        category = get_next_value(lines, "Category")
        if category:
            details["category"] = category

        platform = get_next_value(lines, "Platform")
        if platform:
            details["platform"] = platform

        manufacturer = get_next_value(lines, "Manufacturer")
        if manufacturer:
            details["manufacturer"] = manufacturer

        size = get_next_value(lines, "Size")
        if size:
            details["size"] = size

        version = get_next_value(lines, "Version")
        if version:
            details["version"] = version

        released = get_next_value(lines, "Released")
        if released:
            details["released"] = released

    except requests.RequestException as error:
        print(f"No se pudieron cargar los detalles de {mod['name']}: {error}")

    return details

def send_discord_message(mod):
    details = get_mod_details(mod)

    # Construimos el texto del cuerpo igual al formato exacto de tu captura
    desc_lines = [
        f"**Name:** {mod['name']}",
        f"**Game:** {details['game']}"
    ]

    if details["manufacturer"]:
        desc_lines.append(f"**Manufacturer:** {details['manufacturer']}")

    desc_lines.append(f"**Category:** {details['category']}")
    desc_lines.append(f"**Author:** {details['author']}")

    if details["size"]:
        desc_lines.append(f"**Size:** {details['size']}")

    desc_lines.append(f"**Version:** {details['version']}")

    if details["released"]:
        desc_lines.append(f"**Released:** {details['released']}")

    desc_lines.append(f"**Platform:** {details['platform']}")
    desc_lines.append(f"**Link:** [View Mod]({mod['url']})")

    description_text = "\n".join(desc_lines)

    embed = {
        "title": "25 ModHub - New Mod!",
        "url": mod["url"],
        "description": description_text,
        "color": FS_GREEN,
        "thumbnail": {
            "url": ICON_25_URL
        },
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    if details["image"]:
        embed["image"] = {"url": details["image"]}

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
    else:
        print(f"Mods nuevos detectados: {len(new_ids)}")

    for mod_id in new_ids[:MAX_NEW_MODS]:
        mod = mods[mod_id]

        try:
            send_discord_message(mod)
        except requests.RequestException as error:
            print(f"Error al publicar {mod['name']}: {error}")
            break

        seen[mod_id] = mod
        save_state(seen)
        print(f"Publicado: {mod['name']}")

    for mod_id, mod in mods.items():
        if mod_id in seen:
            seen[mod_id] = {**seen[mod_id], **mod}

    save_state(seen)


if __name__ == "__main__":
    main()
