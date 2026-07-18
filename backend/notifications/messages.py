"""
Warning Lexicon message builder.

Constructs alert text following the Warning Lexicon structure supported by
risk-communication research: an effective warning sequentially states an
authoritative source, the hazard, its location relative to the recipient,
and a protective action. Specific, structured warnings reduce the
verification-seeking delay ("milling") that vague alerts tend to cause.

Messages are produced in English, French, and Arabic according to each
device's registered language.

Note: the French and Arabic strings are functional translations intended
for demonstration and evaluation. Review by a native speaker familiar with
Algerian civil-protection terminology is required before any operational
deployment.
"""

from backend.geo.distance import bearing_deg

# English/French use 16-point compass abbreviations. Arabic uses an 8-point
# scale because the 16-point intercardinal names are long and hard to parse
# quickly under stress when spelled out.
_COMPASS_EN = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]

_COMPASS_FR = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO",
]

_COMPASS_AR_8 = [
    "الشمال", "الشمال الشرقي", "الشرق", "الجنوب الشرقي",
    "الجنوب", "الجنوب الغربي", "الغرب", "الشمال الغربي",
]

# Emergency contact, formatted consistently across all languages.
_CIVIL_PROTECTION = {
    "en": "Civil Protection (14)",
    "fr": "la Protection Civile (14)",
    "ar": "الحماية المدنية (14)",
}


def _compass_16(bearing: float) -> int:
    """Index into a 16-point compass table for the given bearing."""
    return int((bearing + 11.25) % 360 // 22.5)


def _compass_8(bearing: float) -> int:
    """Index into an 8-point compass table for the given bearing."""
    return int((bearing + 22.5) % 360 // 45)


def build_alert_message(
    level: str,
    device_lat: float,
    device_lon: float,
    cluster_lat: float,
    cluster_lon: float,
    distance_km: float,
    language: str = "en",
) -> str:
    """Build a Warning Lexicon-structured alert message.

    The message states, in order: the source (NiDa / Civil Protection), the
    hazard and its distance and compass direction from the recipient, and a
    protective action appropriate to the severity level.
    """
    bearing = bearing_deg(device_lat, device_lon, cluster_lat, cluster_lon)
    dist = round(distance_km, 1)

    if language == "ar":
        direction = _COMPASS_AR_8[_compass_8(bearing)]
        cp = _CIVIL_PROTECTION["ar"]
        header = "تنبيه NiDa — الحماية المدنية"
        hazard = f"حريق غابات نشط على بعد {dist} كم في اتجاه {direction}"
        actions = {
            "critical": f"خطر على الحياة. غادِر الآن مبتعداً عن الحريق واتصل بـ{cp}.",
            "warning": "استعد للمغادرة الآن. اجمع وثائقك وأدويتك وتابع التحديثات.",
            "advisory": "حريق غابات قريب منك. ابقَ يقظاً وتابع التنبيهات.",
        }
        return f"{header} | {hazard} | {actions[level]}"

    if language == "fr":
        direction = _COMPASS_FR[_compass_16(bearing)]
        cp = _CIVIL_PROTECTION["fr"]
        header = "ALERTE NiDa — Protection Civile"
        hazard = f"Feu de forêt actif à {dist} km, direction {direction}"
        actions = {
            "critical": f"Danger de mort. Partez maintenant en vous éloignant du feu et appelez {cp}.",
            "warning": "Préparez-vous à partir maintenant. Rassemblez documents et médicaments, suivez les mises à jour.",
            "advisory": "Feu de forêt détecté près de vous. Restez vigilant et suivez les alertes.",
        }
        return f"{header} | {hazard} | {actions[level]}"

    direction = _COMPASS_EN[_compass_16(bearing)]
    cp = _CIVIL_PROTECTION["en"]
    header = "NiDa ALERT — Civil Protection"
    hazard = f"Active wildfire {dist} km to your {direction}"
    actions = {
        "critical": f"Life-threatening. Leave now, moving away from the fire, and call {cp}.",
        "warning": "Prepare to leave now. Gather documents and medication, and monitor updates.",
        "advisory": "Wildfire detected near you. Stay alert and monitor updates.",
    }
    return f"{header} | {hazard} | {actions[level]}"
