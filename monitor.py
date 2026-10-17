import os
import json
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
    response = requests.get(
        SOURCE,
        headers=HEADERS,
        timeout=30
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")
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
        if name:
            mods[str(mod_id)] = {
                "name": name,
                "url": url
            }

    if not mods:
        raise RuntimeError(
            "No se detectaron mods. Revisa el HTML del ModHub."
        )

    return mods


def send_discord_message(mod):
    payload = {
        "content": (
            "🆕 **¡Nuevo mod de Farming Simulator 25!**\n"
            f"🚜 **{mod['name']}**\n"
            f"🔗 [Ver y descargar en el ModHub oficial]"
            f"({mod['url']})"
        ),
        "allowed_mentions": {"parse": []}
    }

    response = requests.post(
        WEBHOOK,
        json=payload,
        timeout=20
    )
    response.raise_for_status()


def main():
    mods = get_mods()

    if not STATE.exists():
        STATE.write_text(
            json.dumps(mods, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )
        print(f"Configuración inicial: {len(mods)} mods registrados.")
        return

    with STATE.open("r", encoding="utf-8") as file:
        seen = json.load(file)

    if isinstance(seen, list):
        seen = {str(item): {} for item in seen}

    # Si una ficha ya estaba registrada, actualizar sus datos.
    # Los identificadores nuevos se procesan por separado.
    new_ids = [mod_id for mod_id in mods if mod_id not in seen]

    if not new_ids:
        print("No se han detectado mods nuevos.")
        return

    print(f"Mods nuevos detectados: {len(new_ids)}")

    # Procesar un máximo de 10 por ejecución.
    for mod_id in new_ids[:10]:
        mod = mods[mod_id]

        try:
            send_discord_message(mod)
        except requests.RequestException as error:
            print(f"Error al publicar {mod['name']}: {error}")
            break

        # Guardar cada envío confirmado inmediatamente.
        seen[mod_id] = mod
        STATE.write_text(
            json.dumps(seen, indent=2, ensure_ascii=False),
            encoding="utf-8"
        )

        print(f"Publicado: {mod['name']}")

    # Actualizar datos de los mods que ya conocíamos sin
    # marcarlos como nuevas publicaciones.
    for mod_id, mod in mods.items():
        if mod_id in seen:
            seen[mod_id] = mod

    STATE.write_text(
        json.dumps(seen, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


if __name__ == "__main__":
    main()
