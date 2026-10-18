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

FS_GREEN = 0x76B900
MAX_NEW_MODS = 10

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
    """Obtiene autor, categoría, plataformas e imagen de la ficha."""
    details = {
        "author": None,
        "category": None,
        "platform": None,
        "image": mod.get("image"),
    }

    try:
        soup = get_soup(mod["url"])

        # La imagen principal suele estar en los metadatos de la ficha.
        for attribute in (
            {"property": "og:image"},
            {"name": "twitter:image"},
        ):
            meta = soup.find("meta", attrs=attribute)
            if meta and meta.get("content"):
                details["image"] = urljoin(
                    mod["url"], meta["content"]
                )
                break

        # Extraer los datos visibles de la página.
        lines = [
            line.strip()
            for line in soup.get_text("\n").splitlines()
            if line.strip()
        ]

        details["author"] = get_next_value(lines, "Author")
        details["category"] = get_next_value(lines, "Category")
        details["platform"] = get_next_value(lines, "Platform")

    except requests.RequestException as error:
        print(f"No se pudieron cargar los detalles de {mod['name']}: {error}")

    return details


def send_discord_message(mod):
    details = get_mod_details(mod)

    embed = {
        "title": f"🚜 {mod['name']}",
        "url": mod["url"],
        "description": (
            "**Nuevo mod disponible en Farming Simulator 25**\n"
            "Publicado en el ModHub oficial."
        ),
        "color": FS_GREEN,
        "fields": [],
        "footer": {
            "text": "FS25 ModHub • Catálogo de novedades"
        },
    }

    if details["author"]:
        embed["fields"].append({
            "name": "👤 Autor",
            "value": details["author"][:1024],
            "inline": True,
        })

    if details["category"]:
        embed["fields"].append({
            "name": "📁 Categoría",
            "value": details["category"][:1024],
            "inline": True,
        })

    if details["platform"]:
        embed["fields"].append({
            "name": "🎮 Plataformas",
            "value": details["platform"][:1024],
            "inline": False,
        })

    if details["image"]:
        embed["thumbnail"] = {"url": details["image"]}

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

    # Primera ejecución: registrar el estado actual sin enviar avisos.
    if not STATE.exists():
        save_state(mods)
        print(f"Configuración inicial: {len(mods)} mods registrados.")
        return

    with STATE.open("r", encoding="utf-8") as file:
        seen = json.load(file)

    # Compatibilidad con versiones anteriores de seen.json.
    if isinstance(seen, list):
        seen = {str(item): {} for item in seen}

    new_ids = [mod_id for mod_id in mods if mod_id not in seen]

    if not new_ids:
        print("No se han detectado mods nuevos.")
    else:
        print(f"Mods nuevos detectados: {len(new_ids)}")

    # Publicar hasta 10 mods por ejecución.
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

    # Actualizar los datos de los mods conocidos.
    for mod_id, mod in mods.items():
        if mod_id in seen:
            seen[mod_id] = {**seen[mod_id], **mod}

    save_state(seen)


if __name__ == "__main__":
    main()
