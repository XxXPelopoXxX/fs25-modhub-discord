python

import os
import json
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

SOURCE = "https://www.farming-simulator.com/mods.php?filter=latest"
STATE = Path("seen.json")
WEBHOOK = os.environ["DISCORD_WEBHOOK"]

headers = {
    "User-Agent": "FS25-ModHub-Discord-Monitor/1.0"
}

response = requests.get(SOURCE, headers=headers, timeout=30)
response.raise_for_status()
soup = BeautifulSoup(response.text, "html.parser")

# Buscar enlaces a fichas de mods del ModHub.
mods = {}
for link in soup.find_all("a", href=True):
    href = link["href"]
    if "mod.php" not in href or "MORE INFO" not in link.get_text(" ", strip=True).upper():
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

    # Solo fichas identificables del juego FS25.
    if name and re.search(r"mod\.php", url):
        mods[url] = name

if not mods:
    raise RuntimeError(
        "No se detectaron mods. La estructura de la página "
        "puede haber cambiado; no se enviará ningún mensaje."
    )

seen = json.loads(STATE.read_text()) if STATE.exists() else None

# Primera ejecución: registrar lo encontrado sin inundar Discord.
if seen is None:
    STATE.write_text(json.dumps(list(mods.keys()), indent=2))
    print(f"Configuración inicial: registrados {len(mods)} mods.")
else:
    known = set(seen)
    new_mods = [(url, name) for url, name in mods.items()
                if url not in known]

    # Limitar avisos por ejecución.
    for url, name in new_mods[:10]:
        message = {
            "content": f"🆕 **Nuevo mod de FS25: {name}**\n{url}"
        }
        result = requests.post(WEBHOOK, json=message, timeout=20)
        result.raise_for_status()

    updated = list(dict.fromkeys(list(mods.keys()) + list(seen)))[:1000]
    STATE.write_text(json.dumps(updated, indent=2))
    print(f"Mods nuevos publicados: {min(len(new_mods), 10)}")
