#!/usr/bin/env python3
import argparse
import json
import os
import smtplib
import sys
import time
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen


BASE_URL = "https://www.ocp-pharmalia.fr"
DEFAULT_CACHE = ".ocp-product-cache.json"
DEFAULT_ENV = ".env"
DEFAULT_NOTIFY_STATE = ".ocp-notification-state.json"
WATCH_CIPS = [
    "3400926630294",
    "3400930179734",
    "3400926929992",
    "3400928022127",
    "3400930187050",
    "3400936757509",
    "3400936895744",
    "3400936424722",
    "3400930056202",
    "3400937746243",
    "3400930070604",
    "3400927623004",
    "3400930174968",
    "3400935651518",
    "3400932172146",
    "3400937680554",
    "3400934830167",
    "3400939536057",
    "3400930187159",
    "3400949335107",
    "3400930014066",
    "3400930014035",
    "3400930013984",
    "3400930013953",
    "3400936005167",
    "3400938307658",
    "3400930079782",
    "3400931710509",
    "3400936215993",
]


def now_ms():
    return int(time.time() * 1000)


def load_dotenv(path):
    if not path.exists():
        return

    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            if key.startswith("export "):
                key = key[len("export ") :].strip()
            value = value.strip()
            if not key or key in os.environ:
                continue

            if value[:1] in ("'", '"'):
                value = value[1:]
            if value[-1:] in ("'", '"'):
                value = value[:-1]

            os.environ[key] = value


def load_cache(path):
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def normalize_cip(value):
    return "".join(char for char in value if char.isdigit())


def unique_values(values):
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def save_cache(path, cache):
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(cache, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    tmp_path.replace(path)


def load_json_file(path, default):
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json_file(path, data):
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    tmp_path.replace(path)


def build_cookie_header():
    cookie_header = os.environ.get("OCP_COOKIE")
    if cookie_header:
        return cookie_header.strip()

    auth_cookie = os.environ.get("OCP_AUTH_PHARMACIEN")
    if not auth_cookie:
        return None

    auth_cookie = auth_cookie.strip()
    if auth_cookie.startswith("ocp-auth-pharmacien=") or ";" in auth_cookie:
        return auth_cookie

    return f"ocp-auth-pharmacien={auth_cookie}"


def email_config():
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    from_addr = os.environ.get("EMAIL_FROM") or os.environ.get("MAIL_FROM")
    if not from_addr and user and "@" in user:
        from_addr = user
    to_addr = os.environ.get("EMAIL_TO") or from_addr

    if not host or not from_addr or not to_addr:
        return None

    return {
        "host": host,
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": user,
        "password": password,
        "from_addr": from_addr,
        "to_addr": to_addr,
        "use_ssl": os.environ.get("SMTP_SSL", "").lower() in ("1", "true", "yes"),
        "use_tls": os.environ.get("SMTP_TLS", "1").lower() not in ("0", "false", "no"),
    }


def send_email(config, subject, body):
    message = EmailMessage()
    message["From"] = config["from_addr"]
    message["To"] = config["to_addr"]
    message["Subject"] = subject
    message.set_content(body)

    if config["use_ssl"]:
        with smtplib.SMTP_SSL(config["host"], config["port"], timeout=30) as smtp:
            login_smtp(smtp, config)
            smtp.send_message(message)
        return

    with smtplib.SMTP(config["host"], config["port"], timeout=30) as smtp:
        if config["use_tls"]:
            smtp.starttls()
        login_smtp(smtp, config)
        smtp.send_message(message)


def login_smtp(smtp, config):
    if config["user"] and config["password"]:
        smtp.login(config["user"], config["password"])


def request_json(url, cookie_header, accept="application/json, text/javascript, */*; q=0.01", referer=None):
    headers = {
        "accept": accept,
        "accept-language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
        ),
        "x-requested-with": "XMLHttpRequest",
        "cookie": cookie_header,
    }
    if referer:
        headers["referer"] = referer

    request = Request(url, headers=headers)
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read().decode("utf-8")
            content_type = response.headers.get("content-type", "")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"HTTP {exc.code} sur {url}\n{detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Erreur reseau sur {url}: {exc.reason}") from exc

    if "json" not in content_type.lower():
        preview = body[:500].replace("\n", " ")
        raise RuntimeError(f"Reponse non JSON ({content_type}) sur {url}\n{preview}")

    return json.loads(body)


def search_product(cip, cookie):
    query = urlencode(
        {
            "type": "Produits",
            "term": cip,
            "start": 0,
            "size": 20,
            "disponibilite": "false",
            "_": now_ms(),
        }
    )
    url = f"{BASE_URL}/ocp-back/recherche?{query}"
    referer = f"{BASE_URL}/ocp-pharmacien/resultat-recherche/Produits/0/{quote(cip)}?customVarsSiteSrc=2"
    data = request_json(url, cookie, referer=referer)
    hits = data.get("hits") or []
    if not hits:
        raise RuntimeError(f"Aucun produit trouve pour CIP {cip}")

    hit = next((item for item in hits if str(item.get("code")) == cip), hits[0])
    missing = [field for field in ("id", "canal", "marque") if not hit.get(field)]
    if missing:
        raise RuntimeError(f"Produit incomplet pour CIP {cip}: champs manquants {', '.join(missing)}")

    return {
        "id": hit["id"],
        "cip": str(hit.get("code") or cip),
        "nom": hit.get("nom"),
        "canal": hit["canal"],
        "marque": hit["marque"],
        "libelle": hit.get("libelle"),
        "conditionnement": hit.get("conditionnement"),
        "codeInterneOCP": hit.get("codeInterneOCP"),
        "cachedAt": datetime.now().isoformat(timespec="seconds"),
    }


def availability_url(products, quantity):
    ids = "/".join(quote(product["id"], safe="") for product in products)
    canal = quote(products[0]["canal"], safe="")
    marque = quote(products[0]["marque"], safe="")
    return (
        f"{BASE_URL}/ocp-back/produit/{ids}/disponibilite/"
        f"{quantity}/{canal}/{marque}//resultat-recherche?_={now_ms()}"
    )


def availability_from_item(item):
    dispo = item.get("disponibilite") or {}
    livrable = parse_number(item.get("quantiteLivrable"))
    commandable = bool(dispo.get("commandable"))
    in_stock = commandable and livrable > 0

    return {
        "in_stock": in_stock,
        "commandable": commandable,
        "code": dispo.get("code"),
        "message": dispo.get("message"),
        "quantite_livrable": item.get("quantiteLivrable"),
        "quantite_livrable_max": item.get("quantiteLivrableMax"),
        "prix": item.get("prix"),
        "raw": item,
    }


def check_availability_group(products, cookie, quantity):
    referer = (
        f"{BASE_URL}/ocp-pharmacien/resultat-recherche/Produits/0/"
        f"{quote(products[0]['cip'])}?customVarsSiteSrc=2"
    )
    data = request_json(
        availability_url(products, quantity),
        cookie,
        accept="*/*",
        referer=referer,
    )
    if not isinstance(data, list) or not data:
        raise RuntimeError(f"Reponse disponibilite inattendue: {data!r}")

    products_by_id = {product["id"]: product for product in products}
    results = {}
    unmatched = []
    for item in data:
        product_id = item.get("id")
        product = products_by_id.get(product_id)
        if not product:
            unmatched.append(product_id)
            continue
        results[product["cip"]] = availability_from_item(item)

    missing = [product["cip"] for product in products if product["cip"] not in results]
    if missing:
        raise RuntimeError(f"Disponibilite manquante pour: {', '.join(missing)}")
    if unmatched:
        print(f"IDs disponibilite ignores: {', '.join(str(item) for item in unmatched)}", file=sys.stderr)

    return results


def parse_number(value):
    if value is None or value == "":
        return 0.0
    try:
        return float(str(value).replace(",", "."))
    except ValueError:
        return 0.0


def print_status(product, availability):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state = "DISPO" if availability["in_stock"] else "INDISPONIBLE"
    parts = [
        f"[{stamp}]",
        state,
        f"{product.get('cip')} {product.get('nom') or ''}".strip(),
        f"code={availability.get('code')}",
        f"message={availability.get('message')}",
        f"livrable={availability.get('quantite_livrable')}",
        f"max={availability.get('quantite_livrable_max')}",
    ]
    print(" | ".join(parts), flush=True)


def notification_body(product, availability):
    return "\n".join(
        [
            "Produit disponible chez OCP Pharmalia.",
            "",
            f"CIP: {product.get('cip')}",
            f"Nom: {product.get('nom')}",
            f"Code disponibilite: {availability.get('code')}",
            f"Message: {availability.get('message')}",
            f"Quantite livrable: {availability.get('quantite_livrable')}",
            f"Quantite livrable max: {availability.get('quantite_livrable_max')}",
            f"Heure: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        ]
    )


def maybe_notify(product, availability, notify_state, notify_config, notify_enabled):
    if not notify_enabled:
        return

    cip = product.get("cip")
    previous = notify_state.get(cip, {})
    was_in_stock = bool(previous.get("in_stock"))
    is_in_stock = bool(availability["in_stock"])

    if not is_in_stock:
        notify_state[cip] = {
            "in_stock": False,
            "checkedAt": datetime.now().isoformat(timespec="seconds"),
            "lastNotifiedAt": previous.get("lastNotifiedAt"),
        }
        return

    if was_in_stock:
        notify_state[cip] = {
            "in_stock": True,
            "checkedAt": datetime.now().isoformat(timespec="seconds"),
            "lastNotifiedAt": previous.get("lastNotifiedAt"),
        }
        return

    subject = f"OCP dispo: {product.get('nom') or cip}"
    body = notification_body(product, availability)
    if not notify_config:
        print(
            f"Notification mail non configuree pour {cip}: produit disponible.",
            file=sys.stderr,
            flush=True,
        )
        return

    send_email(notify_config, subject, body)
    notify_state[cip] = {
        "in_stock": True,
        "checkedAt": datetime.now().isoformat(timespec="seconds"),
        "lastNotifiedAt": datetime.now().isoformat(timespec="seconds"),
    }
    print(f"Mail envoye pour {cip} -> {notify_config['to_addr']}", flush=True)


def get_product(cip, cookie, cache_path, refresh_cache):
    cache = load_cache(cache_path)
    if not refresh_cache and cip in cache:
        return cache[cip]

    product = search_product(cip, cookie)
    cache[cip] = product
    save_cache(cache_path, cache)
    return product


def load_products(cips, cookie_header, cache_path, refresh_cache):
    products = []
    for cip in cips:
        try:
            product = get_product(cip, cookie_header, cache_path, refresh_cache)
        except Exception as exc:
            print(f"Erreur recherche {cip}: {exc}", file=sys.stderr, flush=True)
            continue

        products.append(product)
        print(
            f"Produit: {product.get('cip')} | {product.get('nom')} | "
            f"id={product.get('id')} | cache={cache_path}",
            flush=True,
        )

    return products


def product_groups(products):
    groups = {}
    for product in products:
        key = (product["canal"], product["marque"])
        groups.setdefault(key, []).append(product)
    return groups.values()


def check_products(
    products,
    cookie_header,
    quantity,
    show_json,
    notify_state,
    notify_config,
    notify_enabled,
):
    ok = 0
    for group in product_groups(products):
        try:
            availabilities = check_availability_group(group, cookie_header, quantity)
        except Exception as exc:
            cips = ", ".join(product["cip"] for product in group)
            print(f"Erreur disponibilite {cips}: {exc}", file=sys.stderr, flush=True)
            continue

        for product in group:
            availability = availabilities[product["cip"]]
            print_status(product, availability)
            maybe_notify(product, availability, notify_state, notify_config, notify_enabled)
            if show_json:
                print(json.dumps(availability["raw"], ensure_ascii=False, indent=2), flush=True)
            ok += 1

    return ok


def main():
    parser = argparse.ArgumentParser(
        description="Surveille la disponibilite OCP Pharmalia d'un produit par CIP."
    )
    parser.add_argument(
        "cip",
        nargs="*",
        help="CIP optionnels. Sans argument, utilise WATCH_CIPS dans le fichier.",
    )
    parser.add_argument(
        "-i",
        "--interval",
        type=float,
        default=5,
        help="Intervalle entre deux controles, en minutes. Defaut: 5.",
    )
    parser.add_argument(
        "-q",
        "--quantity",
        type=int,
        default=1,
        help="Quantite demandee dans l'URL disponibilite. Defaut: 1.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Fait un seul controle puis s'arrete.",
    )
    parser.add_argument(
        "--refresh-cache",
        action="store_true",
        help="Force la premiere requete recherche meme si le CIP est deja en cache.",
    )
    parser.add_argument(
        "--cache",
        default=DEFAULT_CACHE,
        help=f"Chemin du cache produit. Defaut: {DEFAULT_CACHE}.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Affiche la reponse disponibilite brute en JSON apres le statut.",
    )
    parser.add_argument(
        "--env",
        default=DEFAULT_ENV,
        help=f"Chemin du fichier .env. Defaut: {DEFAULT_ENV}.",
    )
    parser.add_argument(
        "--notify-state",
        default=DEFAULT_NOTIFY_STATE,
        help=f"Chemin de l'etat des notifications. Defaut: {DEFAULT_NOTIFY_STATE}.",
    )
    parser.add_argument(
        "--no-email",
        action="store_true",
        help="Desactive l'envoi de mail meme si un produit est disponible.",
    )
    args = parser.parse_args()

    input_cips = args.cip or WATCH_CIPS
    cips = unique_values(normalize_cip(value) for value in input_cips)
    if not cips or any(not cip for cip in cips):
        parser.error("Chaque CIP doit contenir au moins un chiffre.")
    if args.interval <= 0:
        parser.error("--interval doit etre superieur a 0.")
    if args.quantity <= 0:
        parser.error("--quantity doit etre superieure a 0.")

    load_dotenv(Path(args.env))

    cookie_header = build_cookie_header()
    if not cookie_header:
        print(
            "Variable OCP_AUTH_PHARMACIEN ou OCP_COOKIE manquante. "
            "Exemple: export OCP_AUTH_PHARMACIEN='...'",
            file=sys.stderr,
        )
        return 2

    cache_path = Path(args.cache)
    notify_state_path = Path(args.notify_state)
    notify_state = load_json_file(notify_state_path, {})
    notify_config = email_config()
    try:
        products = load_products(cips, cookie_header, cache_path, args.refresh_cache)
        if not products:
            return 1

        while True:
            ok = check_products(
                products,
                cookie_header,
                args.quantity,
                args.json,
                notify_state,
                notify_config,
                not args.no_email,
            )
            save_json_file(notify_state_path, notify_state)
            if args.once:
                return 0 if ok else 1
            time.sleep(args.interval * 60)
    except KeyboardInterrupt:
        print("\nArret demande.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Erreur: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
