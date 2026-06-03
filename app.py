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

import socket
def get_base_url():
    if os.getenv('RENDER'):
        return f"https://{os.getenv('RENDER_EXTERNAL_HOSTNAME')}"
    return request.host_url.rstrip('/')

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
    city = request.args.get("city", "").strip()
    currency_from = request.args.get("currency_from", "COP")
    amount = request.args.get("amount", 100000, type=float)

    if not city:
        return jsonify({"error": "Falta el parámetro: city"}), 400

    # ========== 1. GEOCODING ==========
    lat, lon, country_code, timezone = geocode_city(city)

    # ========== 2. CLIMA ==========
    weather_data = {}
    if OPENWEATHER_API_KEY:
        try:
            r = requests.get(
                "https://api.openweathermap.org/data/2.5/weather",
                params={"q": f"{city},{country_code}" if country_code else city,
                        "appid": OPENWEATHER_API_KEY, "units": "metric", "lang": "es"},
                timeout=10
            )
            if r.status_code == 200:
                d = r.json()
                weather_data = {
                    "city": d.get("name", city),
                    "country": d.get("sys", {}).get("country", ""),
                    "temperature": d.get("main", {}).get("temp"),
                    "feels_like": d.get("main", {}).get("feels_like"),
                    "humidity": d.get("main", {}).get("humidity"),
                    "wind_speed": d.get("wind", {}).get("speed"),
                    "description": d.get("weather", [{}])[0].get("description", ""),
                    "icon_url": f"https://openweathermap.org/img/wn/{d.get('weather', [{}])[0].get('icon', '')}@2x.png",
                }
        except Exception as e:
            weather_data = {"error": str(e)}

    # ========== 3. PAÍS ==========
    country_info = {}
    if country_code:
        try:
            r = requests.get(f"https://restcountries.com/v3.1/alpha/{country_code}", timeout=10)
            if r.status_code == 200:
                d = r.json()[0]
                currencies = d.get("currencies", {})
                currency_info = {}
                for k, v in currencies.items():
                    currency_info = {"code": k, "name": v.get("name", ""), "symbol": v.get("symbol", "")}
                    break
                languages = d.get("languages", {})
                language = list(languages.values())[0] if languages else ""
                country_info = {
                    "name": d.get("name", {}).get("common", ""),
                    "official_name": d.get("name", {}).get("official", ""),
                    "capital": d.get("capital", [""])[0],
                    "currency": currency_info,
                    "language": language,
                    "region": d.get("region", ""),
                    "subregion": d.get("subregion", ""),
                    "timezone": d.get("timezones", [""])[0],
                    "flag_url": d.get("flags", {}).get("png", ""),
                    "population": d.get("population", 0),
                }
        except Exception as e:
            country_info = {"error": str(e)}

    # ========== 4. FOTOS ==========
    photos_data = {"photos": []}
    if UNSPLASH_ACCESS_KEY:
        try:
            r = requests.get(
                "https://api.unsplash.com/search/photos",
                headers={"Authorization": f"Client-ID {UNSPLASH_ACCESS_KEY}"},
                params={"query": city, "per_page": 5, "orientation": "landscape"},
                timeout=10
            )
            if r.status_code == 200:
                d = r.json()
                photos_data = {
                    "city": city,
                    "total_found": d.get("total", 0),
                    "photos": [{
                        "url": p.get("urls", {}).get("regular"),
                        "thumb_url": p.get("urls", {}).get("thumb"),
                        "description": p.get("description") or p.get("alt_description") or city,
                        "photographer": p.get("user", {}).get("name"),
                        "photographer_url": p.get("user", {}).get("links", {}).get("html"),
                    } for p in d.get("results", [])]
                }
        except Exception as e:
            photos_data = {"error": str(e)}

    # ========== 5. MONEDA ==========
    target_currency = country_info.get("currency", {}).get("code", "USD")
    currency_data = {}
    try:
        r = requests.get(f"https://open.er-api.com/v6/latest/{currency_from}", timeout=10)
        if r.status_code == 200:
            d = r.json()
            if target_currency in d.get("rates", {}):
                rate = d["rates"][target_currency]
                currency_data = {
                    "from": currency_from,
                    "to": target_currency,
                    "amount": amount,
                    "converted": round(amount * rate, 2),
                    "rate": rate,
                    "date": d.get("time_last_update_utc", "")[:16],
                }
    except Exception as e:
        currency_data = {"error": str(e)}

    # ========== 6. LUGARES TURÍSTICOS ==========
    places_list = []
    if lat and lon:
        try:
            query = f"""
            [out:json][timeout:20];
            (
              node["tourism"](around:15000,{lat},{lon});
              node["historic"](around:15000,{lat},{lon});
              node["leisure"="park"](around:15000,{lat},{lon});
              way["tourism"](around:15000,{lat},{lon});
              way["historic"](around:15000,{lat},{lon});
              relation["tourism"](around:15000,{lat},{lon});
            );
            out center limit 15;
            """
            op = requests.post("https://overpass-api.de/api/interpreter", data={"data": query}, timeout=25)
            if op.status_code == 200:
                elements = op.json().get("elements", [])
                seen = set()
                for el in elements:
                    tags = el.get("tags", {})
                    name = tags.get("name:es") or tags.get("name", "").strip()
                    if not name or len(name) < 3 or name in seen:
                        continue
                    seen.add(name)
                    
                    if el.get("type") == "node":
                        p_lat, p_lon = el.get("lat"), el.get("lon")
                    else:
                        center = el.get("center", {})
                        p_lat, p_lon = center.get("lat"), center.get("lon")
                    
                    if not p_lat:
                        continue
                    
                    place_type = "attraction"
                    icon = "📍"
                    type_label = tags.get("tourism", tags.get("historic", "Lugar Turístico"))
                    
                    if "museum" in str(tags).lower():
                        icon = "🖼️"
                        place_type = "museum"
                    elif "park" in str(tags).lower():
                        icon = "🌳"
                        place_type = "park"
                    elif "church" in str(tags).lower() or "cathedral" in str(tags).lower():
                        icon = "⛪"
                        place_type = "place_of_worship"
                    elif "castle" in str(tags).lower() or "fort" in str(tags).lower():
                        icon = "🏰"
                        place_type = "castle"
                    elif "monument" in str(tags).lower():
                        icon = "🗿"
                        place_type = "monument"
                    
                    places_list.append({
                        "name": name,
                        "type": place_type,
                        "icon": icon,
                        "label": type_label.capitalize() if type_label else "Lugar Turístico",
                        "address": tags.get("addr:street", ""),
                        "website": tags.get("website", ""),
                        "wikipedia": "",
                        "lat": p_lat,
                        "lon": p_lon,
                    })
                    
                    if len(places_list) >= 8:
                        break
        except Exception as e:
            print(f"Error places: {e}")
    
    # Lugares por defecto si no encuentra nada
    if not places_list:
        default_places = {
            "oslo": [
                {"name": "Ópera de Oslo", "icon": "🎭", "label": "Teatro"},
                {"name": "Parque Vigeland", "icon": "🗿", "label": "Parque"},
                {"name": "Museo del Barco Vikingo", "icon": "🚢", "label": "Museo"},
                {"name": "Fortaleza de Akershus", "icon": "🏰", "label": "Castillo"},
                {"name": "Palacio Real", "icon": "👑", "label": "Palacio"},
            ],
            "paris": [
                {"name": "Torre Eiffel", "icon": "🗼", "label": "Monumento"},
                {"name": "Museo del Louvre", "icon": "🖼️", "label": "Museo"},
                {"name": "Catedral de Notre Dame", "icon": "⛪", "label": "Catedral"},
                {"name": "Arco del Triunfo", "icon": "🏛️", "label": "Monumento"},
                {"name": "Montmartre", "icon": "🎨", "label": "Barrio"},
            ],
            "london": [
                {"name": "Big Ben", "icon": "🕰️", "label": "Monumento"},
                {"name": "London Eye", "icon": "🎡", "label": "Mirador"},
                {"name": "Museo Británico", "icon": "🏛️", "label": "Museo"},
                {"name": "Torre de Londres", "icon": "🏰", "label": "Castillo"},
                {"name": "Buckingham Palace", "icon": "👑", "label": "Palacio"},
            ],
            "madrid": [
                {"name": "Palacio Real", "icon": "👑", "label": "Palacio"},
                {"name": "Museo del Prado", "icon": "🖼️", "label": "Museo"},
                {"name": "Parque del Retiro", "icon": "🌳", "label": "Parque"},
                {"name": "Plaza Mayor", "icon": "🏛️", "label": "Plaza"},
                {"name": "Puerta del Sol", "icon": "📍", "label": "Plaza"},
            ],
            "barcelona": [
                {"name": "Sagrada Familia", "icon": "⛪", "label": "Basílica"},
                {"name": "Park Güell", "icon": "🌳", "label": "Parque"},
                {"name": "Casa Batlló", "icon": "🏠", "label": "Arquitectura"},
                {"name": "Ramblas", "icon": "🚶", "label": "Paseo"},
            ],
        }
        
        city_lower = city.lower()
        default_list = []
        for key, places in default_places.items():
            if key in city_lower:
                default_list = places
                break
        
        if not default_list:
            default_list = [
                {"name": "Plaza Principal", "icon": "🏛️", "label": "Plaza"},
                {"name": "Catedral", "icon": "⛪", "label": "Catedral"},
                {"name": "Museo de la Ciudad", "icon": "🖼️", "label": "Museo"},
                {"name": "Parque Central", "icon": "🌳", "label": "Parque"},
                {"name": "Mirador", "icon": "🔭", "label": "Mirador"},
            ]
        
        for place in default_list[:8]:
            places_list.append({
                "name": place["name"],
                "type": "attraction",
                "icon": place["icon"],
                "label": place["label"],
                "address": "",
                "website": "",
                "wikipedia": "",
                "lat": lat,
                "lon": lon,
            })

    # ========== 7. WIKIPEDIA ==========
    wiki_data = {}
    
    # Limpiar nombre para Wikipedia
    wiki_city = city.replace(" ", "_")
    
    try:
        # Intentar español
        url = f"https://es.wikipedia.org/api/rest_v1/page/summary/{wiki_city}"
        r = requests.get(url, timeout=8, headers={"User-Agent": "TravelScope/2.0"})
        if r.status_code == 200:
            d = r.json()
            if d.get("extract") and len(d.get("extract", "")) > 100:
                wiki_data = {
                    "title": d.get("title", city),
                    "summary": d.get("extract", "")[:600],
                    "image_url": (d.get("thumbnail") or {}).get("source", ""),
                    "wiki_url": d.get("content_urls", {}).get("desktop", {}).get("page", ""),
                }
    except:
        pass
    
    # Si no, probar inglés
    if not wiki_data:
        try:
            url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{wiki_city}"
            r = requests.get(url, timeout=8, headers={"User-Agent": "TravelScope/2.0"})
            if r.status_code == 200:
                d = r.json()
                if d.get("extract"):
                    wiki_data = {
                        "title": d.get("title", city),
                        "summary": d.get("extract", "")[:600],
                        "image_url": (d.get("thumbnail") or {}).get("source", ""),
                        "wiki_url": d.get("content_urls", {}).get("desktop", {}).get("page", ""),
                    }
        except:
            pass
    
    # Buscar con search API
    if not wiki_data:
        try:
            params = {
                "action": "query",
                "list": "search",
                "srsearch": city,
                "format": "json",
                "srlimit": 1
            }
            r = requests.get("https://es.wikipedia.org/w/api.php", params=params, timeout=8)
            if r.status_code == 200:
                results = r.json().get("query", {}).get("search", [])
                if results:
                    title = results[0]["title"]
                    url = f"https://es.wikipedia.org/api/rest_v1/page/summary/{title.replace(' ', '_')}"
                    r = requests.get(url, timeout=8)
                    if r.status_code == 200:
                        d = r.json()
                        wiki_data = {
                            "title": d.get("title", city),
                            "summary": d.get("extract", "")[:600],
                            "image_url": (d.get("thumbnail") or {}).get("source", ""),
                            "wiki_url": d.get("content_urls", {}).get("desktop", {}).get("page", ""),
                        }
        except:
            pass
    
    # Fallback final
    if not wiki_data:
        wiki_data = {
            "title": city,
            "summary": f"{city} es una ciudad fascinante con rica historia y cultura. Visita el enlace para más información.",
            "image_url": "",
            "wiki_url": f"https://es.wikipedia.org/wiki/{wiki_city}",
        }

    # ========== 8. HORA ACTUAL ==========
    time_data = {}
    if lat and lon:
        try:
            from timezonefinder import TimezoneFinder
            tf = TimezoneFinder()
            tz_str = tf.timezone_at(lat=lat, lng=lon)
            
            if not tz_str:
                geo_r = requests.get(
                    f"http://api.geonames.org/timezoneJSON",
                    params={"lat": lat, "lng": lon, "username": "demo"},
                    timeout=8
                )
                if geo_r.status_code == 200:
                    tz_str = geo_r.json().get("timezoneId", "UTC")
                else:
                    tz_str = "UTC"
            
            try:
                tz = pytz.timezone(tz_str)
                now = datetime.now(tz)
                time_data = {
                    "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "timezone": tz_str,
                    "date": now.strftime("%A, %d de %B de %Y"),
                    "time": now.strftime("%H:%M:%S"),
                    "is_daytime": 6 <= now.hour < 18
                }
            except:
                now = datetime.now(pytz.UTC)
                time_data = {
                    "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
                    "timezone": "UTC",
                    "date": now.strftime("%A, %d de %B de %Y"),
                    "time": now.strftime("%H:%M:%S"),
                    "is_daytime": 6 <= now.hour < 18
                }
        except Exception as e:
            print(f"Error time: {e}")
            now = datetime.now(pytz.UTC)
            time_data = {
                "datetime": now.strftime("%Y-%m-%d %H:%M:%S"),
                "timezone": "UTC",
                "date": now.strftime("%A, %d de %B de %Y"),
                "time": now.strftime("%H:%M:%S"),
                "is_daytime": 6 <= now.hour < 18
            }

    # ========== RESPUESTA FINAL ==========
    return jsonify({
        "query": {"city": city, "country": country_code, "currency_from": currency_from, "amount": amount},
        "weather": weather_data,
        "country": country_info,
        "photos": photos_data,
        "currency": currency_data,
        "wiki": wiki_data,
        "places": {"places": places_list, "lat": lat, "lon": lon},
        "time": time_data,
    })
    
@app.route("/convert")
def convert():
    currency_from = request.args.get("currency_from", "USD").upper()
    currency_to = request.args.get("currency_to", "EUR").upper()
    amount = request.args.get("amount", 1.0, type=float)
    
    try:
        r = requests.get(f"https://api.exchangerate-api.com/v4/latest/{currency_from}", timeout=8)
        if r.status_code == 200:
            data = r.json()
            rates = data.get("rates", {})
            if currency_to in rates:
                rate = rates[currency_to]
                converted = round(amount * rate, 2)
                return jsonify({
                    "from": currency_from,
                    "to": currency_to,
                    "amount": amount,
                    "converted": converted,
                    "rate": rate,
                    "date": data.get("date", ""),
                    "success": True
                })
        
        r = requests.get(f"https://open.er-api.com/v6/latest/{currency_from}", timeout=8)
        if r.status_code == 200:
            data = r.json()
            rates = data.get("rates", {})
            if currency_to in rates:
                rate = rates[currency_to]
                converted = round(amount * rate, 2)
                return jsonify({
                    "from": currency_from,
                    "to": currency_to,
                    "amount": amount,
                    "converted": converted,
                    "rate": rate,
                    "date": data.get("time_last_update_utc", ""),
                    "success": True
                })
        
        return jsonify({"error": "Moneda no soportada", "success": False}), 400
        
    except Exception as e:
        return jsonify({"error": str(e), "success": False}), 500
