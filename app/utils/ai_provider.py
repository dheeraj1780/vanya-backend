"""
Calls Google Gemini's vision API for species identification and diagnosis,
and its text API for species-scoped care calculations.
Set GEMINI_API_KEY in .env and this calls the actual API — nothing here is
mocked or hardcoded.
"""
import base64
import json
from typing import Any, Dict, Optional

from google import genai
from google.genai import types
from google.genai.errors import APIError

from app.core.config import get_settings
from app.core.exceptions import BadRequestError, ExternalProviderError

settings = get_settings()

_client: genai.Client | None = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.gemini_api_key)
    return _client


def _decode_image(image_base64: str) -> bytes:
    try:
        return base64.b64decode(image_base64, validate=True)
    except Exception as exc:
        raise BadRequestError(f"image_base64 is not valid base64: {exc}") from exc


def _parse_json_response(raw_text: str) -> Dict[str, Any]:
    """Strips markdown code fences if Gemini wrapped its JSON in them, then
    parses. Raises BadRequestError (not a 500) if the model's response
    genuinely isn't valid JSON, since that's most often caused by a
    malformed/unsupported input image, not a server bug."""
    cleaned = raw_text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise BadRequestError(f"Could not parse AI response as JSON: {exc}") from exc


IDENTIFY_PROMPT = (
    "Identify this houseplant. First judge whether the photo actually shows a real, living plant — "
    "not an artificial/plastic/silk plant, a toy, a drawing or photo-of-a-photo, or any non-plant "
    "object/animal/person. Respond ONLY with valid JSON, no other text, in this exact "
    'shape: {"is_real_plant": boolean, "fun_message": string, "species": string, "common_name": '
    'string, "regional_names": array of up to 4 strings, "confidence": "high"|"medium"|"low", '
    '"water_frequency_days": number, "light_needs": string (max 4 words), "care_note": string '
    '(max 20 words), "soil_type": string (max 15 words), '
    '"fun_facts": array of 4 strings, each max 25 words, genuinely interesting '
    'and specific to this species (not generic plant-care tips), '
    '"is_indoor": boolean (true if commonly grown as a houseplant, false if primarily outdoor/garden), '
    '"is_pet_safe": boolean (true only if non-toxic to cats and dogs if ingested — err toward false if uncertain), '
    '"is_air_purifying": boolean (true if scientifically recognized for measurable air-purifying effect, '
    'not just marketing claims), '
    '"care_difficulty": "easy"|"moderate"|"hard" (based on how forgiving the plant is of missed '
    'watering, variable light, and general neglect)}. '
    'regional_names: this app\'s users are mostly in India — list the common household/vernacular '
    'names this plant actually goes by there (Hindi and other widely-used regional names), e.g. '
    '"Money Plant"/"Paisa Paudha" for Epipremnum aureum, "Tulsi" for holy basil, "Kadi Patta" for '
    'curry leaf, "Ghritkumari" for Aloe vera. Give real, commonly-used names only — if this species '
    'genuinely has no distinct household name in India, return an empty array rather than inventing '
    'one. common_name/species stay in English/Latin as usual; regional_names is purely additive. '
    'soil_type: one practical, plain-language recommendation a beginner could act on (e.g. '
    '"Well-draining potting mix with perlite and compost" or "Sandy, well-drained soil"), not a '
    'botanical description. '
    'fun_facts: write for a total beginner with no botany background — plain everyday language, no '
    'unexplained Latin/technical jargon, nothing generic ("water regularly") — genuinely specific and '
    'interesting facts about this exact species. '
    'If is_real_plant is false: set fun_message to one short, warm, playful sentence (max 30 words) '
    'reacting specifically to what the image actually shows (a fake plant, a mug, a cat, whatever it '
    'is) — never scold or sound like an error message. Every other field still needs a real value '
    '(species/common_name can name what it looks like, e.g. "Artificial fern" or "Coffee mug"; use '
    'reasonable defaults for the rest, regional_names can be empty) since the app always expects them, '
    'but the client only shows fun_message to the user in this case. '
    'If is_real_plant is true but you cannot identify the exact species with reasonable confidence, '
    'set confidence to "low", leave fun_message as an empty string, and give your best general guess, '
    'but still provide your best-effort values for every other field.'
)


async def identify_plant(image_base64: str) -> Dict[str, Any]:
    try:
        image_bytes = _decode_image(image_base64)
        client = _get_client()
        response = await client.aio.models.generate_content(
            model=settings.ai_model,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                IDENTIFY_PROMPT,
            ],
        )
        return _parse_json_response(response.text)
    except BadRequestError:
        raise
    except APIError as exc:
        raise ExternalProviderError(f"AI provider could not identify the plant: {exc}") from exc
    except Exception as exc:
        raise ExternalProviderError(f"Unexpected error calling AI provider: {exc}") from exc


def _diagnose_prompt(species: str) -> str:
    return (
        f'This is a {species}. First image is the whole plant, second is a close-up of the '
        'problem area. Diagnose what\'s wrong. Respond ONLY with valid JSON: '
        '{"confidence": "high"|"medium"|"low", "likely_causes": [string, string] (max 2, ranked '
        'most to least likely), "recommended_action": string (max 30 words), "urgency": '
        '"low"|"medium"|"high"}. Never give a single overconfident answer when symptoms are '
        'ambiguous — list the top 2 causes if uncertain.'
    )


async def diagnose_plant(species: str, full_plant_image_base64: str, closeup_image_base64: str) -> Dict[str, Any]:
    try:
        full_plant_bytes = _decode_image(full_plant_image_base64)
        closeup_bytes = _decode_image(closeup_image_base64)
        client = _get_client()
        response = await client.aio.models.generate_content(
            model=settings.ai_model,
            contents=[
                types.Part.from_bytes(data=full_plant_bytes, mime_type="image/jpeg"),
                types.Part.from_bytes(data=closeup_bytes, mime_type="image/jpeg"),
                _diagnose_prompt(species),
            ],
        )
        return _parse_json_response(response.text)
    except BadRequestError:
        raise
    except APIError as exc:
        raise ExternalProviderError(f"AI provider could not diagnose the plant: {exc}") from exc
    except Exception as exc:
        raise ExternalProviderError(f"Unexpected error calling AI provider: {exc}") from exc


def _calculator_prompt(
    species: Optional[str],
    light_needs: Optional[str],
    care_difficulty: Optional[str],
    water_frequency_days: int,
    season: str,
    temperature_c: Optional[float],
    pot_diameter_cm: Optional[int],
    room_light: Optional[str],
) -> str:
    return (
        f'You are a houseplant care expert. Plant: {species or "unknown species"} (recorded light '
        f'needs: {light_needs or "unknown"}, care difficulty: {care_difficulty or "unknown"}, current '
        f'baseline watering every {water_frequency_days} days). Current conditions: season={season}'
        f'{f", outdoor temperature={temperature_c}C" if temperature_c is not None else ""}, '
        f'pot diameter={pot_diameter_cm or "unspecified"}cm, room light={room_light or "unspecified"}. '
        "Respond ONLY with valid JSON, no other text, in this exact shape: "
        '{"adjusted_interval_days": number, "recommended_amount_ml": number, '
        '"watering_reasoning": string (max 30 words), "fertilizer_needed": boolean, '
        '"fertilizer_dilution_ratio": string or null, "fertilizer_amount_ml": number or null, '
        '"fertilizer_frequency_days": number or null, "fertilizer_reasoning": string (max 30 words), '
        '"light_fit": "ideal"|"acceptable"|"poor"|"unknown", "light_fit_reasoning": string (max 30 words)}. '
        "Base every number on this specific species' real horticultural needs and the given "
        "conditions, not generic one-size-fits-all houseplant advice. "
        'fertilizer_dilution_ratio must be a short "X:Y" ratio of parts fertilizer to parts water (e.g. '
        '"1:10"), assuming an ordinary balanced liquid houseplant fertilizer — never a vague phrase like '
        '"half strength" with no baseline given. fertilizer_amount_ml is the volume of the diluted '
        "mixture to give (same idea as a normal watering amount), not raw undiluted concentrate — say so "
        "explicitly in fertilizer_reasoning so this doesn't read as a bare, unexplained ratio."
    )


async def calculate_care(
    species: Optional[str],
    light_needs: Optional[str],
    care_difficulty: Optional[str],
    water_frequency_days: int,
    season: str,
    temperature_c: Optional[float],
    pot_diameter_cm: Optional[int],
    room_light: Optional[str],
) -> Dict[str, Any]:
    try:
        client = _get_client()
        response = await client.aio.models.generate_content(
            model=settings.ai_model,
            contents=[
                _calculator_prompt(
                    species, light_needs, care_difficulty, water_frequency_days,
                    season, temperature_c, pot_diameter_cm, room_light,
                )
            ],
        )
        return _parse_json_response(response.text)
    except APIError as exc:
        raise ExternalProviderError(f"AI provider could not compute care calculators: {exc}") from exc
    except Exception as exc:
        raise ExternalProviderError(f"Unexpected error calling AI provider: {exc}") from exc
