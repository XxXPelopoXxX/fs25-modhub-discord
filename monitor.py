
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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; FS25ModHubMonitor/1.0)"
}

MAX_MODS_PER_RUN = 10
EMBED_COLOR = 0x43B581

session = requests.Session()
session.headers.update(HEADERS)


def get_page(url):
    response = session.get(url, timeout=30)
    response.raise_for_status()
    return response.text


def meta_content(soup, *selectors):
    for selector in selectors:
        tag = soup.select_one(selector)
        if tag:
            value = tag.get("content", "").strip()
            if value:
                return value
    return None


def get_mod_details(mod):
    """Obtiene imagen, descripción y autor de la ficha del mod."""
    details = {
        "image": mod.get("image"),
        "description": None,
        "author": mod.get("author"),
    }

    try:
        soup = BeautifulSoup(get_page(mod["url"]), "html.parser")

        # Imagen oficial de la ficha.
        image = meta_content(
            soup,
            'meta[property="og:image"]',
            'meta[name="twitter:image"]',
        )

        if image:
            details["image"] = urljoin(mod["url"], image)

        # Descripción de la página.
        details["description"] = meta_content(
            soup,
            'meta[property="og:description"]',
            'meta[name="description"]',
        )

        # Intento de encontrar el autor en el texto.
        if not details["author"]:
            text = soup.get_text(" ", strip=True)

            match = re.search(
                r"\b(?:By|Por|Von|Par)\s*:\s*(.{2,100}?)"
                r"(?=\s+(?:MORE INFO|MODHUB|DOWNLOAD|"
                r"Farming Simulator|Category|Categoría)\b|$)",
                text,
                re.IGNORECASE,
            )

            if match:
                details["author"] = match.group(1).strip(" |,-")

    except (requests.RequestException, ValueError) as error:
        print(
            f"No se pudieron obtener detalles de "
            f"{mod['name']}: {error}"
        )

    # Discord necesita una URL válida para la miniatura.
    if details["image"]:
        parsed_image = urlparse(details["image"])

        if parsed_image.scheme != "https":
            details["image"] = None

    return details


def get_mods():
    soup = BeautifulSoup(get_page(SOURCE), "html.parser")
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

        # Buscar el contenedor que incluye el nombre del mod.
        for _ in range(6):
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

        # Intentar obtener una miniatura desde el listado.
        image = None
        img = card.find("img", src=True)

        if img:
            image = urljoin(SOURCE, img["src"])

        mods[str(mod_id)] = {
            "name": name,
            "url": url,
            "image": image,
            "author": None,
        }

    if not mods:
        raise RuntimeError(
            "No se detectaron mods. Revisa el HTML del ModHub."
        )

    return mods


def send_discord_message(mod):
    details = get_mod_details(mod)

    description = details.get("description") or ""
    description = description.strip()[:250]

    # Diseño compacto de Discord.
    embed = {
        "title": f"🚜 {mod['name']}"[:256],
        "url": mod["url"],
        "color": EMBED_COLOR,
        "footer": {
            "text": "FS25 ModHub"
        },
    }

    if description:
        embed["description"] = description

    author = details.get("author")

    if author:
        embed["author"] = {
            "name": f"👤 {author}"[:256]
        }

    image = details.get("image")

    if image:
        embed["thumbnail"] = {
            "url": image
        }

    payload = {
        "content": "🆕 **Nuevo mod disponible para Farming Simulator 25**",
        "embeds": [embed],
        "allowed_mentions": {
            "parse": []
        },
    }

    response = session.post(
        WEBHOOK,
        json=payload,
        timeout=20,
    )
    response.raise_for_status()


def main():
    mods = get_mods()

    # Primera ejecución: registrar el estado inicial sin
    # publicar todos los mods existentes.
    if not STATE.exists():
        STATE.write_text(
            json.dumps(mods, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        print(
            f"Configuración inicial: {len(mods)} mods registrados."
        )
        return

    with STATE.open("r", encoding="utf-8") as file:
        seen = json.load(file)

    # Compatibilidad con el formato antiguo de seen.json.
    if isinstance(seen, list):
        seen = {
            str(item): {}
            for item in seen
        }

    new_ids = [
        mod_id
        for mod_id in mods
        if mod_id not in seen
    ]

    if not new_ids:
        print("No se han detectado mods nuevos.")
        return

    print(f"Mods nuevos detectados: {len(new_ids)}")

    # Procesar un máximo de 10 mods por ejecución.
    for mod_id in new_ids[:MAX_MODS_PER_RUN]:
        mod = mods[mod_id]

        try:
            send_discord_message(mod)

        except requests.RequestException as error:
            print(
                f"Error al publicar {mod['name']}: {error}"
            )
            break

        # Registrar el mod solo después del envío confirmado.
        seen[mod_id] = mod

        STATE.write_text(
            json.dumps(seen, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        print(f"Publicado: {mod['name']}")

    # Actualizar los datos de mods conocidos sin volver
    # a anunciarlos como nuevas publicaciones.
    for mod_id, mod in mods.items():
        if mod_id in seen:
            previous = seen[mod_id]

            if isinstance(previous, dict):
                seen[mod_id] = {
                    **previous,
                    **mod,
                }
            else:
                seen[mod_id] = mod

    STATE.write_text(
        json.dumps(seen, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()

