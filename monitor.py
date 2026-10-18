import os
import re
import json
import requests
from pathlib import Path
from datetime import datetime, timedelta, timezone
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# URL principal de novedades
SOURCE = "https://www.farming-simulator.com/mods.php?filter=latest"
STATE = Path("seen.json")
WEBHOOK = os.getenv("DISCORD_WEBHOOK")

MAX_AGE_DAYS = 7
MAX_NOTIFICATIONS = 10
TIMEOUT = 25

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )
}


def get_soup(url):
    response = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def load_state():
    """Carga el estado guardado {mod_id: version}."""
    if not STATE.exists():
        return None

    try:
        data = json.loads(STATE.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return {str(item): "1.0.0.0" for item in data}
        if isinstance(data, dict):
            seen_data = data.get("seen", data)
            if isinstance(seen_data, list):
                return {str(item): "1.0.0.0" for item in seen_data}
            if isinstance(seen_data, dict):
                return {str(k): str(v) for k, v in seen_data.items()}
    except (json.JSONDecodeError, OSError):
        print("Aviso: no se pudo leer seen.json. Se iniciará un estado nuevo.")

    return {}


def save_state(seen):
    """Guarda el diccionario de estados {mod_id: version}."""
    STATE.write_text(
        json.dumps({"seen": seen}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_mods():
    """Obtiene los mods y prefabs listados en la página de novedades de ModHub."""
    soup = get_soup(SOURCE)
    mods = {}

    for link in soup.select('a[href*="mod.php?mod_id="]'):
        href = link.get("href", "")
        match = re.search(r"mod_id=(\d+)", href)

        if not match:
            continue

        mod_id = match.group(1)
        url = urljoin(SOURCE, href)

        container = link
        for _ in range(8):
            if container.parent:
                container = container.parent

            text = container.get_text(" ", strip=True)
            image = container.find("img")

            if text and image:
                break

        name = ""
        heading = container.find(["h1", "h2", "h3", "h4"])

        if heading:
            name = heading.get_text(" ", strip=True)

        if not name:
            name = link.get_text(" ", strip=True)

        if not name:
            image = container.find("img")
            if image:
                name = image.get("alt", "").strip()

        if not name:
            name = f"Mod {mod_id}"

        image_url = ""
        image = container.find("img")

        if image:
            image_url = image.get("src", "")
            if image_url:
                image_url = urljoin(SOURCE, image_url)

        mods[mod_id] = {
            "id": mod_id,
            "name": name[:250],
            "url": url,
            "image": image_url,
        }

    print(f"Elementos encontrados en el listado (Mods/Prefabs): {len(mods)}")
    return mods


def parse_date(value):
    """Intenta convertir una fecha a datetime con zona horaria UTC."""
    if not value:
        return None

    value = value.strip()

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except ValueError:
        pass

    for fmt in (
        "%Y-%m-%d",
        "%d.%m.%Y",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d-%m-%Y",
    ):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue

    return None


def extract_publication_date(soup):
    """Busca una fecha explícita."""
    for tag in soup.find_all("meta"):
        key = (
            tag.get("property")
            or tag.get("name")
            or tag.get("itemprop")
            or ""
        ).lower()

        if key in {
            "article:published_time",
            "datepublished",
            "date",
            "pubdate",
        }:
            result = parse_date(tag.get("content", ""))
            if result:
                return result

    for tag in soup.find_all("time"):
        result = parse_date(tag.get("datetime", "") or tag.get_text(" ", strip=True))
        if result:
            return result

    text = soup.get_text(" ", strip=True)
    patterns = [
        r"(?:published|release date|date published|added|released|publicado|fecha de publicación)\s*:?\s*(\d{4}-\d{2}-\d{2})",
        r"(?:published|release date|date published|added|released|publicado|fecha de publicación)\s*:?\s*(\d{1,2}[./-]\d{1,2}[./-]\d{4})",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            result = parse_date(match.group(1))
            if result:
                return result

    return None


def get_mod_details(mod):
    """Obtiene detalles, fecha de publicación, versión y detecta si es un Prefab."""
    try:
        soup = get_soup(mod["url"])
    except requests.RequestException as exc:
        print(f"No se pudo consultar {mod['name']}: {exc}")
        return None

    title = soup.find("h1")
    if title:
        candidate = title.get_text(" ", strip=True)
        if candidate:
            mod["name"] = candidate[:250]

    for tag in soup.find_all("meta"):
        key = (tag.get("property") or tag.get("name") or "").lower()

        if key == "og:image" and tag.get("content"):
            mod["image"] = urljoin(mod["url"], tag["content"])
            break

    mod["published_at"] = extract_publication_date(soup)
    mod["author"] = "No disponible"
    mod["category"] = "No disponible"
    mod["platform"] = "No disponible"
    mod["version"] = "1.0.0.0"

    text = soup.get_text("\n", strip=True)
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    for index, line in enumerate(lines[:-1]):
        label = line.lower().rstrip(":")
        value = lines[index + 1][:200]

        if label in {"author", "modder", "creador", "autor"}:
            mod["author"] = value
        elif label in {"category", "categoría", "categoria"}:
            mod["category"] = value
        elif label in {"platform", "platforms", "plataforma"}:
            mod["platform"] = value
        elif label in {"version", "versión"}:
            mod["version"] = value

    # Detectar explícitamente si es un Prefab basándose en la categoría o en el nombre
    is_prefab = False
    cat_lower = mod["category"].lower()
    name_lower = mod["name"].lower()
    if "prefab" in cat_lower or "prefab" in name_lower:
        is_prefab = True
    
    mod["is_prefab"] = is_prefab
    return mod


def is_recent(mod):
    published_at = mod.get("published_at")

    if not published_at:
        print(f"Se ignora {mod['name']}: fecha de publicación no verificable.")
        return False

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=MAX_AGE_DAYS)

    if published_at > now + timedelta(days=1):
        print(f"Se ignora {mod['name']}: la fecha parece estar en el futuro.")
        return False

    if published_at < cutoff:
        print(f"Se ignora {mod['name']}: publicación antigua ({published_at.date()}).")
        return False

    return True


def send_discord(mod, is_update=False):
    if not WEBHOOK:
        raise RuntimeError(
            "Falta la variable de entorno DISCORD_WEBHOOK."
        )

    is_prefab = mod.get("is_prefab", False)

    # Definir diseño según el tipo de contenido (Prefab, Actualización o Nuevo Mod)
    if is_update:
        title_prefix = "🔄 [ACTUALIZACIÓN]"
        desc_text = f"¡El elemento **{mod['name']}** se ha actualizado a la versión **{mod['version']}**!"
        embed_color = 0xF1C40F  # Amarillo
    elif is_prefab:
        title_prefix = "📦 [PREFAB]"
        desc_text = f"¡Nuevo Prefab disponible en el ModHub!"
        embed_color = 0x3498DB  # Azul distintivo para Prefabs
    else:
        title_prefix = "🚜 [NUEVO MOD]"
        desc_text = f"¡Nuevo mod disponible en Farming Simulator 25!"
        embed_color = 0x2ECC71  # Verde para nuevos mods

    embed = {
        "title": f"{title_prefix} {mod['name']}"[:256],
        "url": mod["url"],
        "description": (
            f"{desc_text}\n"
            f"[Ver detalle en ModHub]({mod['url']})"
        ),
        "color": embed_color,
        "fields": [
            {
                "name": "Autor",
                "value": str(mod.get("author", "No disponible"))[:1024],
                "inline": True,
            },
            {
                "name": "Categoría",
                "value": str(mod.get("category", "No disponible"))[:1024],
                "inline": True,
            },
            {
                "name": "Versión",
                "value": str(mod.get("version", "1.0.0.0"))[:1024],
                "inline": True,
            },
            {
                "name": "Plataforma",
                "value": str(mod.get("platform", "No disponible"))[:1024],
                "inline": True,
            },
            {
                "name": "Publicado / Actualizado",
                "value": mod["published_at"].strftime("%d/%m/%Y") if mod.get("published_at") else "No disponible",
                "inline": True,
            },
        ],
    }

    if mod.get("image"):
        embed["thumbnail"] = {"url": mod["image"]}

    response = requests.post(
        WEBHOOK,
        json={"embeds": [embed]},
        timeout=TIMEOUT,
    )
    response.raise_for_status()


def main():
    mods = get_mods()

    if not mods:
        print("No se encontraron elementos. No se modifica el estado.")
        return

    seen = load_state()

    if seen is None:
        seen = {}
        for mod_id in mods:
            seen[mod_id] = "1.0.0.0"
        save_state(seen)
        print(
            f"Primera ejecución: se registraron {len(seen)} elementos "
            "sin enviar notificaciones."
        )
        return

    sent_count = 0

    for mod_id, basic_mod in mods.items():
        details = get_mod_details(basic_mod)

        if not details:
            continue

        current_version = details.get("version", "1.0.0.0")
        saved_version = seen.get(mod_id)

        is_new = mod_id not in seen
        is_update = not is_new and saved_version != current_version

        if not is_new and not is_update:
            continue

        if not is_recent(details):
            seen[mod_id] = current_version
            save_state(seen)
            continue

        try:
            send_discord(details, is_update=is_update)
            tipo_msg = "Prefab" if details.get("is_prefab") else "Mod"
            action_str = f"Actualización de {tipo_msg}" if is_update else f"Nuevo {tipo_msg}"
            print(f"{action_str} enviado: {details['name']} (Versión: {current_version})")
            
            seen[mod_id] = current_version
            save_state(seen)
            sent_count += 1
        except (requests.RequestException, RuntimeError) as exc:
            print(f"Error enviando {details['name']}: {exc}")
            continue

        if sent_count >= MAX_NOTIFICATIONS:
            break

    print(f"Proceso terminado. Notificaciones enviadas: {sent_count}")


if __name__ == "__main__":
    main()
