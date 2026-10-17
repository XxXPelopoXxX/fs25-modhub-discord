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


def get_mods():
    response = requests.get(SOURCE, headers=HEADERS, timeout=30)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    mods = {}

    for link in soup.find_all("a", href=True):
        href = link["href"]
        parsed = urlparse(urljoin(SOURCE, href))

        if not parsed.path.endswith("/mod.php"):
            continue

        params = parse_qs(parsed.query)
        mod_id = params.get("mod_id", [None])[0]
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
        url = urljoin(SOURCE, href)
        mods[mod_id] = {"name": name, "url": url}

    if not mods:
        raise RuntimeError(
            "No se detectaron fichas de mods. "
            "Hay que revisar el HTML actual del ModHub."
        )

    return mods


def main():
    mods = get_mods()
    seen = json.loads(STATE.read_text()) if STATE.exists() else None

    # Primera ejecución: guardar el catálogo inicial sin enviar mensajes.
    if seen is None:
        STATE.write_text(json.dumps(mods, indent=2))
        print(f"Configuración inicial: {len(mods)} mods registrados.")
        return

    # Compatibilidad con el formato guardado en ejecuciones anteriores.
    if isinstance(seen, list):
        seen = {str(item): {} for item in seen}

    new_ids = [mod_id for mod_id in mods if mod_id not in seen]

    for mod_id in new_ids[:10]:
        mod = mods[mod_id]
        message = {
            "content": (
                f"🆕 **Nuevo mod de FS25: {mod['name']}**\n"
                f"{mod['url']}"
            )
        }
        result = requests.post(WEBHOOK, json=message, timeout=20)
        result.raise_for_status()
        print(f"Publicado: {mod['name']}")

    # Guardar los mods detectados para no repetir los avisos.
    seen.update(mods)
    STATE.write_text(json.dumps(seen, indent=2))


if __name__ == "__main__":
    main()
