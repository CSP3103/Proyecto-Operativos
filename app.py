import os
import re
import requests
from flask import Flask, jsonify, request, render_template
from flask_cors import CORS
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from datetime import datetime
import pytz
from timezonefinder import TimezoneFinder

load_dotenv()

app = Flask(__name__)
CORS(app)

# ============ CONFIGURACIÓN ============
OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY")
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY")
GEOAPIFY_API_KEY = os.getenv("GEOAPIFY_API_KEY")
PORT = int(os.getenv("PORT", 10000))

# ============ TIPOS DE LUGARES (mejorado) ============
TYPE_META = {
    "attraction":       ("🌟", "Atracción Icónica"),
    "monument":         ("🗿", "Monumento"),
    "memorial":         ("🏅", "Memorial"),
    "viewpoint":        ("🔭", "Mirador"),
    "park":             ("🌿", "Parque"),
    "place_of_worship": ("⛪", "Templo / Iglesia"),
    "castle":           ("🏰", "Castillo / Fortaleza"),
    "museum":           ("🖼️", "Museo"),
    "ruins":            ("🏛️", "Ruinas Históricas"),
    "theatre":          ("🎭", "Teatro"),
    "default":          ("📍", "Punto de Interés"),
}

OVERPASS_TAGS = [
    ("tourism",  "attraction"),
    ("historic", "monument"),
    ("historic", "memorial"),
    ("tourism",  "viewpoint"),
    ("historic", "castle"),
    ("tourism",  "museum"),
    ("historic", "ruins"),
    ("amenity",  "theatre"),
    ("leisure",  "park"),
    ("amenity",  "place_of_worship"),
]

# ============ RUTA PRINCIPAL ============
@app.route("/")
def index():
    return render_template("index.html")

@app.route("/health")
def health():
    return jsonify({"status": "ok", "service": "TravelScope", "version": "3.0.0"})

# ============ GEOCODING INTELIGENTE ============
def geocode_city(city, country=""):
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": f"{city},{country}" if country else city,
                    "format": "json", "limit": 5, "addressdetails": 1},
            headers={"User-Agent": "TravelScope/2.0"},
            timeout=10,
        )
        results = r.json()

        # Priorizar por type, SIN el OR que rompe todo
        priority = ["city", "town", "village", "municipality", "administrative"]
        for p in priority:
            for res in results:
                if res.get("type") == p:  # ← solo esta condición
                    lat  = float(res["lat"])
                    lon  = float(res["lon"])
                    code = res.get("address", {}).get("country_code", "").upper()
                    timezone = get_timezone(lat, lon)
                    return lat, lon, code, timezone

        # Fallback: primer resultado
        if results:
            lat  = float(results[0]["lat"])
            lon  = float(results[0]["lon"])
            code = results[0].get("address", {}).get("country_code", "").upper()
            timezone = get_timezone(lat, lon)
            return lat, lon, code, timezone

    except Exception:
        pass
    return None, None, "", "UTC"

_tf = TimezoneFinder()  # instancia global, es costosa de crear

def get_timezone(lat, lon):
    try:
        tz = _tf.timezone_at(lat=lat, lng=lon)
        print(f"DEBUG timezone → lat:{lat}, lon:{lon} → {tz}")  # quítalo después
        return tz if tz else "UTC"
    except Exception:
        return "UTC"

# ============ HORA ACTUAL ============
@app.route("/current-time")
def current_time():
    city = request.args.get("city", "")
    country = request.args.get("country", "")
    
    if not city:
        return jsonify({"error": "Falta el parámetro: city"}), 400
    
    lat, lon, _, timezone_str = geocode_city(city, country)
    
    if not lat or not lon:
        return jsonify({"error": "Ciudad no encontrada"}), 404
    
    try:
        tz = pytz.timezone(timezone_str)
        now = datetime.now(tz)
        
        return jsonify({
            "city": city,
            "timezone": timezone_str,
            "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),  # Hora LOCAL de la ciudad
            "date": now.strftime("%A, %d de %B de %Y"),
            "time": now.strftime("%H:%M:%S"),
            "hour_12": now.strftime("%I:%M:%S %p"),
            "weekday": now.strftime("%A"),
            "utc_offset": now.strftime("%z"),
            "is_daytime": 6 <= now.hour < 18
        })
    except Exception as e:
        return jsonify({"error": f"Error obteniendo hora: {str(e)}"}), 503
        return jsonify({"error": f"Error obteniendo hora: {str(e)}"}), 503

# ============ PAÍS ============
@app.route("/country")
def country():
    code = request.args.get("code", "").upper()
    if not code:
        return jsonify({"error": "Falta el parámetro: code"}), 400
    try:
        r = requests.get(f"https://restcountries.com/v3.1/alpha/{code}", timeout=10)
        r.raise_for_status()
        d = r.json()[0]
        currencies = d.get("currencies", {})
        currency_info = {}
        for k, v in currencies.items():
            currency_info = {"code": k, "name": v.get("name", ""), "symbol": v.get("symbol", "")}
            break
        languages = d.get("languages", {})
        language = list(languages.values())[0] if languages else ""
        return jsonify({
            "name":          d["name"]["common"],
            "official_name": d["name"]["official"],
            "capital":       d.get("capital", [""])[0],
            "currency":      currency_info,
            "language":      language,
            "region":        d.get("region", ""),
            "subregion":     d.get("subregion", ""),
            "timezone":      d.get("timezones", [""])[0],
            "flag_url":      d.get("flags", {}).get("png", ""),
            "population":    d.get("population", 0),
        })
    except requests.exceptions.HTTPError as e:
        return jsonify({"error": f"RestCountries error {e.response.status_code}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 503

# ============ CLIMA ============
@app.route("/weather")
def weather():
    city    = request.args.get("city")
    country = request.args.get("country", "")
    if not city:
        return jsonify({"error": "Falta el parámetro: city"}), 400
    try:
        r = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": f"{city},{country}" if country else city,
                    "appid": OPENWEATHER_API_KEY, "units": "metric", "lang": "es"},
            timeout=10,
        )
        r.raise_for_status()
        d = r.json()
        return jsonify({
            "city":        d["name"],
            "country":     d["sys"]["country"],
            "temperature": d["main"]["temp"],
            "feels_like":  d["main"]["feels_like"],
            "humidity":    d["main"]["humidity"],
            "wind_speed":  d["wind"]["speed"],
            "description": d["weather"][0]["description"],
            "icon_url":    f"https://openweathermap.org/img/wn/{d['weather'][0]['icon']}@2x.png",
        })
    except requests.exceptions.HTTPError as e:
        return jsonify({"error": f"OpenWeather error {e.response.status_code}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 503

# ============ FOTOS ============
@app.route("/photos")
def photos():
    city  = request.args.get("city")
    count = min(max(request.args.get("count", 5, type=int), 1), 10)
    if not city:
        return jsonify({"error": "Falta el parámetro: city"}), 400
    try:
        r = requests.get(
            "https://api.unsplash.com/search/photos",
            headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"},
            params={"query": city, "per_page": count, "orientation": "landscape"},
            timeout=10,
        )
        r.raise_for_status()
        d = r.json()
        photos_list = [{
            "url":              p["urls"]["regular"],
            "thumb_url":        p["urls"]["thumb"],
            "description":      p.get("description") or p.get("alt_description") or city,
            "photographer":     p["user"]["name"],
            "photographer_url": p["user"]["links"]["html"],
        } for p in d.get("results", [])]
        return jsonify({"city": city, "total_found": d.get("total", 0), "photos": photos_list})
    except requests.exceptions.HTTPError as e:
        return jsonify({"error": f"Unsplash error {e.response.status_code}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 503

# ============ MONEDA ============
@app.route("/currency")
def currency():
    from_cur = request.args.get("from", "USD").upper()
    to_cur   = request.args.get("to", "EUR").upper()
    amount   = request.args.get("amount", 1.0, type=float)
    try:
        r = requests.get(f"https://open.er-api.com/v6/latest/{from_cur}", timeout=10)
        r.raise_for_status()
        d = r.json()
        if to_cur not in d.get("rates", {}):
            return jsonify({"error": f"Moneda '{to_cur}' no soportada"}), 400
        rate      = d["rates"][to_cur]
        converted = round(amount * rate, 4)
        return jsonify({
            "from":      from_cur,
            "to":        to_cur,
            "amount":    amount,
            "converted": converted,
            "rate":      rate,
            "date":      d.get("time_last_update_utc", "")[:16],
        })
    except requests.exceptions.HTTPError as e:
        return jsonify({"error": f"ExchangeRate error {e.response.status_code}"}), 502
    except Exception as e:
        return jsonify({"error": str(e)}), 503

# ============ WIKIPEDIA ============
WIKI_HEADERS = {"User-Agent": "TravelScope/2.0 (educational project)"}

def wiki_summary(term, lang="es"):
    """Busca en Wikipedia REST API el resumen de un artículo, con fallback al motor de búsqueda."""
    url = f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(term)}"
    try:
        r = requests.get(url, headers=WIKI_HEADERS, timeout=10)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    # Fallback: motor de búsqueda de Wikipedia
    try:
        s = requests.get(
            f"https://{lang}.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search", "srsearch": term,
                    "format": "json", "srlimit": 1},
            headers=WIKI_HEADERS, timeout=8,
        )
        results = s.json().get("query", {}).get("search", [])
        if results:
            title = results[0]["title"]
            r2 = requests.get(
                f"https://{lang}.wikipedia.org/api/rest_v1/page/summary/{requests.utils.quote(title)}",
                headers=WIKI_HEADERS, timeout=10,
            )
            if r2.status_code == 200:
                return r2.json()
    except Exception:
        pass
    return None

def wiki_related_articles(city, lang="es"):
    """Obtiene artículos relacionados con la ciudad (turismo, historia, cultura)."""
    try:
        r = requests.get(
            f"https://{lang}.wikipedia.org/w/api.php",
            params={"action": "query", "list": "search",
                    "srsearch": f"{city} turismo historia cultura",
                    "format": "json", "srlimit": 5, "srnamespace": 0},
            headers=WIKI_HEADERS, timeout=8,
        )
        results = r.json().get("query", {}).get("search", [])
        articles = []
        for res in results[:4]:
            title   = res.get("title", "")
            snippet = res.get("snippet", "").replace('<span class="searchmatch">', "").replace("</span>", "")
            if title and city.lower() not in title.lower()[:5]:
                articles.append({"title": title, "snippet": snippet[:120] + "..."})
        return articles
    except Exception:
        return []

@app.route("/wiki")
def wiki():
    city    = request.args.get("city", "")
    country = request.args.get("country", "")
    if not city:
        return jsonify({"error": "Falta el parámetro: city"}), 400

    # Español primero, con fallback a inglés
    data = wiki_summary(city, lang="es")
    if not data or data.get("type") == "disambiguation":
        data = wiki_summary(f"{city} {country}".strip(), lang="es")
    if not data:
        data = wiki_summary(city, lang="en")

    if not data:
        return jsonify({"error": "No se encontró información en Wikipedia"}), 404

    related = wiki_related_articles(city, lang="es")

    return jsonify({
        "city":        city,
        "title":       data.get("title", city),
        "summary":     data.get("extract", "")[:600],
        "image_url":   (data.get("thumbnail") or {}).get("source", ""),
        "wiki_url":    data.get("content_urls", {}).get("desktop", {}).get("page", ""),
        "related":     related,
        "description": data.get("description", ""),
    })

# ============ LUGARES TURÍSTICOS (MEJORADO con el código que me mostraste) ============
@app.route("/places")
def places():
    city    = request.args.get("city", "")
    country = request.args.get("country", "")
    limit   = min(max(request.args.get("limit", 8, type=int), 1), 20)
    # Acepta lat/lon precalculados desde main-service para evitar doble geocoding
    try:
        lat = float(request.args.get("lat", 0) or 0)
        lon = float(request.args.get("lon", 0) or 0)
    except Exception:
        lat, lon = 0.0, 0.0

    if not city:
        return jsonify({"error": "Falta el parámetro: city"}), 400

    # Si no llegaron coordenadas, geocodificar aquí
    if not lat or not lon:
        lat, lon, _, _ = geocode_city(city, country)
        if not lat or not lon:
            return jsonify({"error": "Ciudad no encontrada"}), 404

    # --- Overpass API: landmarks icónicos reales ---
    filters = "\n".join([
        f'  node["{k}"="{v}"]["name"](around:15000,{lat},{lon});\n'
        f'  way["{k}"="{v}"]["name"](around:15000,{lat},{lon});'
        for k, v in OVERPASS_TAGS
    ])
    overpass_query = f"""[out:json][timeout:25];
(
{filters}
);
out center tags 60;"""

    places_list = []
    try:
        op = requests.post(
            "https://overpass-api.de/api/interpreter",
            data={"data": overpass_query},
            timeout=25,
        )
        op.raise_for_status()
        elements = op.json().get("elements", [])

        seen = set()
        scored = []
        for el in elements:
            tags = el.get("tags", {})
            name = (tags.get("name:es") or tags.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)

            if el.get("type") == "node":
                p_lat, p_lon = el.get("lat"), el.get("lon")
            else:
                c = el.get("center", {})
                p_lat, p_lon = c.get("lat"), c.get("lon")
            if not p_lat:
                continue

            place_type = "default"
            priority = 99
            for idx, (k, v) in enumerate(OVERPASS_TAGS):
                if tags.get(k) == v:
                    place_type = v
                    priority = idx
                    break

            # Sistema de puntuación para mostrar los más relevantes primero
            score = priority
            if tags.get("wikipedia"): score -= 6  # Tiene artículo en Wikipedia
            if tags.get("wikidata"):  score -= 3  # Tiene entrada en Wikidata
            if tags.get("website"):   score -= 1  # Tiene sitio web oficial
            
            # Limpiar el slug de Wikipedia (ej: "es:Estatua de la Libertad" -> "Estatua de la Libertad")
            wiki_raw = tags.get("wikipedia", "")
            wiki_slug = ""
            if wiki_raw:
                if ":" in wiki_raw:
                    wiki_slug = wiki_raw.split(":", 1)[-1]
                else:
                    wiki_slug = wiki_raw

            scored.append({
                "name":      name,
                "type":      place_type,
                "icon":      TYPE_META.get(place_type, TYPE_META["default"])[0],
                "label":     TYPE_META.get(place_type, TYPE_META["default"])[1],
                "address":   tags.get("addr:street", "") or "",
                "website":   tags.get("website", tags.get("contact:website", "")),
                "wikipedia": wiki_slug,
                "lat": p_lat, 
                "lon": p_lon,
                "_score": score,
            })

        scored.sort(key=lambda x: x["_score"])
        places_list = [{k: v for k, v in p.items() if k != "_score"} for p in scored[:limit]]

    except Exception as e:
        print(f"Overpass error: {e}")
        pass  # Intentar fallback Geoapify

    # --- Fallback Geoapify si Overpass no dio resultados ---
    if not places_list:
        try:
            gp = requests.get(
                "https://api.geoapify.com/v2/places",
                params={
                    "categories": "tourism.sights,entertainment.museum,tourism.attraction",
                    "filter": f"circle:{lon},{lat},12000",
                    "limit": limit,
                    "apiKey": GEOAPIFY_API_KEY,
                },
                timeout=12,
            )
            gp.raise_for_status()
            for f in gp.json().get("features", []):
                props = f.get("properties", {})
                nm = props.get("name", "").strip()
                if nm:
                    places_list.append({
                        "name":      nm,
                        "type":      "attraction",
                        "icon":      "🌟",
                        "label":     "Atracción Turística",
                        "address":   props.get("formatted", ""),
                        "website":   props.get("website", ""),
                        "wikipedia": "",
                        "lat": f["geometry"]["coordinates"][1],
                        "lon": f["geometry"]["coordinates"][0],
                    })
        except Exception as e:
            print(f"Geoapify error: {e}")
            pass

    return jsonify({"city": city, "lat": lat, "lon": lon, "places": places_list})

# ============ ORQUESTADOR /explore ============
def _fetch(name, url, params):
    try:
        r = requests.get(url, params=params, timeout=20)
        r.raise_for_status()
        return name, r.json()
    except Exception as e:
        return name, {"error": str(e)}

@app.route("/explore")
def explore():
    city          = request.args.get("city", "").strip()
    currency_from = request.args.get("currency_from", "COP")
    amount        = request.args.get("amount", 100000, type=float)

    if not city:
        return jsonify({"error": "Falta el parámetro: city"}), 400

    # Geocoding inteligente (fuente de verdad para lat/lon y país)
    lat, lon, country_code, timezone = geocode_city(city)

    # Datos del país
    target_currency = "USD"
    country_info    = {}
    if country_code:
        try:
            c_res = requests.get(
                f"{request.host_url}country", params={"code": country_code}, timeout=8
            ).json()
            if "error" not in c_res and c_res.get("official_name"):
                country_info    = c_res
                target_currency = country_info.get("currency", {}).get("code", "USD")
        except Exception:
            pass

    base = request.host_url.rstrip("/")

    calls = {
        "weather":  (f"{base}/weather",        {"city": city, "country": country_code}),
        "photos":   (f"{base}/photos",         {"city": city, "count": 5}),
        "currency": (f"{base}/currency",       {"from": currency_from, "to": target_currency, "amount": amount}),
        "places":   (f"{base}/places",         {"city": city, "country": country_code, "lat": lat or "", "lon": lon or "", "limit": 8}),
        "wiki":     (f"{base}/wiki",           {"city": city, "country": country_info.get("name", "")}),
        "time":     (f"{base}/current-time",   {"city": city, "country": country_code}),
    }

    results = {"country": country_info, "timezone": timezone}
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {executor.submit(_fetch, name, url, params): name for name, (url, params) in calls.items()}
        for future in as_completed(futures):
            name, data = future.result()
            results[name] = data

    return jsonify({
        "query":   {"city": city, "country": country_code, "currency_from": currency_from, "amount": amount},
        "weather": results.get("weather", {}),
        "country": results.get("country", {}),
        "photos":  results.get("photos", {}),
        "currency": results.get("currency", {}),
        "wiki":    results.get("wiki", {}),
        "places":  {"places": results.get("places", {}).get("places", []), "lat": lat, "lon": lon},
        "time":    results.get("time", {}),
    })

@app.route("/convert")
def convert():
    currency_from = request.args.get("currency_from", "COP")
    currency_to   = request.args.get("currency_to", "USD")
    amount        = request.args.get("amount", 1.0, type=float)
    try:
        r = requests.get(
            f"{request.host_url}currency",
            params={"from": currency_from, "to": currency_to, "amount": amount},
            timeout=8,
        )
        return jsonify(r.json())
    except Exception as e:
        return jsonify({"error": str(e)}), 500

