
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
    """Obtiene detalles adicionales sin impedir el envío si falla."""
    details = {
        "image": mod.get("image"),
        "description": None,
        "author": mod.get("author"),
    }

    try:
        soup = BeautifulSoup(get_page(mod["url"]), "html.parser")

        # Imagen principal oficial de la ficha.
        image = meta_content(
            soup,
            'meta[property="og:image"]',
            'meta[name="twitter:image"]',
        )

        if image:
            details["image"] = urljoin(mod["url"], image)

        # Descripción para el embed.
        details["description"] = meta_content(
            soup,
            'meta[property="og:description"]',
            'meta[name="description"]',
        )

        # Buscar el autor en el texto de la ficha.
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
        print(f"No se pudieron ampliar los datos de "
              f"{mod['name']}: {error}")

    # Discord necesita imágenes accesibles por HTTPS.
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

        image = None
        img = card.find("img", src=True)

        if img:
            image = urljoin(SOURCE, img["src"])

        # Intentar recuperar el autor visible en la tarjeta.
        author = None
        card_text = card.get_text(" ", strip=True)

        match = re.search(
            r"\b(?:By|Por|Von|Par)\s*:\s*(.{2,100}?)"
            r"(?=\s+\d(?:[.,]\d)?\s*\(|$)",
            card_text,
            re.IGNORECASE,
        )

        if match:
            author = match.group(1).strip(" |,-")

        mods[str(mod_id)] = {
            "name": name,
            "url": url,
            "image": image,
            "author": author,
        }

    if not mods:
        raise RuntimeError(
            "No se detectaron mods. Revisa el HTML del ModHub."
        )

    return mods


def send_discord_message(mod):
    details = get_mod_details(mod)

    description = details.get("description") or (
        "Ya disponible en el ModHub oficial de "
        "Farming Simulator 25."
    )

    # Evitar exceder el límite de descripción de Discord.
    description = description[:4000]

    embed = {
        "title": f"🚜 {mod['name']}"[:256],
        "url": mod["url"],
        "description": description,
        "color": EMBED_COLOR,
        "fields": [
            {
                "name": "🎮 Juego",
                "value": "Farming Simulator 25",
                "inline": True,
            },
        ],
        "footer": {
            "text": "FS25 ModHub • Nueva publicación",
        },
    }

    author = details.get("author")
    if author:
        embed["fields"].append({
            "name": "👤 Autor",
            "value": author[:1024],
            "inline": True,
        })

    image = details.get("image")
    if image:
        embed["image"] = {"url": image}

    payload = {
        "content": "🆕 **¡Nuevo mod disponible!**",
        "embeds": [embed],
        "allowed_mentions": {"parse": []},
    }

    response = session.post(
        WEBHOOK,
        json=payload,
        timeout=20,
    )
    response.raise_for_status()


def main():
    mods = get_mods()

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

    if isinstance(seen, list):
        seen = {str(item): {} for item in seen}

    new_ids = [
        mod_id for mod_id in mods
        if mod_id not in seen
    ]

    if not new_ids:
        print("No se han detectado mods nuevos.")
        return

    print(f"Mods nuevos detectados: {len(new_ids)}")

    for mod_id in new_ids[:MAX_MODS_PER_RUN]:
        mod = mods[mod_id]

        try:
            send_discord_message(mod)
        except requests.RequestException as error:
            print(f"Error al publicar {mod['name']}: {error}")
            break

        # Registrar solo después de confirmar el envío.
        seen[mod_id] = mod
        STATE.write_text(
            json.dumps(seen, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        print(f"Publicado: {mod['name']}")

    # Actualizar los datos conocidos sin anunciar mods antiguos.
    for mod_id, mod in mods.items():
        if mod_id in seen:
            seen[mod_id] = {
                **seen[mod_id],
                **mod,
            }

    STATE.write_text(
        json.dumps(seen, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
