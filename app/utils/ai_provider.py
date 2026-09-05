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
    "Identify this houseplant. First judge whether the photo actually shows a real, living, growing "
    "plant — not an artificial/plastic/silk plant, a toy, a drawing or photo-of-a-photo, harvested "
    "produce on its own (a cut fruit or vegetable with no leaves/stem/growing plant visible — e.g. a "
    "banana or tomato sitting on a counter is food, not a photo of the plant it grew on), or any other "
    "non-plant object/animal/person. Respond ONLY with valid JSON, no other text, in this exact "
    'shape: {"is_real_plant": boolean, "fun_message": string, "species": string, "common_name": '
    'string, "regional_names": array of up to 4 strings, "confidence": "high"|"medium"|"low", '
    '"water_frequency_days": number, "light_needs": string (max 4 words), "care_note": string '
    '(max 20 words), "soil_type": string (max 4 words), "soil_amendments": string (max 20 words), '
    '"fun_facts": array of 4 strings, each max 25 words, genuinely interesting '
    'and specific to this species (not generic plant-care tips), '
    '"is_indoor": boolean (true if commonly grown as a houseplant, false if primarily outdoor/garden), '
    '"is_pet_safe": boolean (true only if non-toxic to cats and dogs if ingested — err toward false if uncertain), '
    '"is_air_purifying": boolean (true if scientifically recognized for measurable air-purifying effect, '
    'not just marketing claims), '
    '"care_difficulty": "easy"|"moderate"|"hard" (based on how forgiving the plant is of missed '
    'watering, variable light, and general neglect)}. '
    'Every free-text field (fun_message, care_note, soil_amendments, fun_facts) must be written for a '
    'total beginner with zero botany background — plain everyday words, no unexplained Latin or '
    'technical jargon, nothing generic ("water regularly") — someone who has never grown a plant before '
    'should immediately understand every sentence. '
    'regional_names: this app\'s users are mostly in India — list the common household/vernacular '
    'names this plant actually goes by there (Hindi and other widely-used regional names), e.g. '
    '"Money Plant"/"Paisa Paudha" for Epipremnum aureum, "Tulsi" for holy basil, "Kadi Patta" for '
    'curry leaf, "Ghritkumari" for Aloe vera. Give real, commonly-used names only — if this species '
    'genuinely has no distinct household name in India, return an empty array rather than inventing '
    'one. common_name/species stay in English/Latin as usual; regional_names is purely additive. '
    'soil_type: name an actual soil type this plant thrives in, the way a home gardener in India '
    'would shop for or describe it — e.g. "Red soil", "Black soil", "Sandy loam", "Laterite soil", '
    '"Alluvial soil", or (for a plant normally grown in a pot rather than the ground) "Well-draining '
    'potting mix". Pick whichever term is actually the best real answer for this species\' natural '
    'preference — not a vague description, a named type. '
    'soil_amendments: what to mix INTO that base soil_type for this species to grow at its best — '
    'e.g. "Add compost, sand and cocopeat for drainage and moisture retention" or "Mix in perlite and '
    'organic manure". Practical and specific to this plant\'s actual needs (drainage, moisture '
    'retention, nutrients, pH), not generic filler. '
    'If is_real_plant is false: set fun_message to one short, warm, playful sentence (max 30 words) '
    'reacting specifically to what the image actually shows (a fake plant, a mug, a cut banana, '
    'whatever it is) — never scold or sound like an error message. Every other field still needs a '
    'real value (species/common_name can name what it looks like, e.g. "Artificial fern" or "Banana '
    '(fruit, not the plant)"; use reasonable defaults for the rest, regional_names can be empty) since '
    'the app always expects them, but the client only shows fun_message to the user in this case. '
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


IDENTIFY_BY_NAME_PROMPT_PREFIX = (
    "A user who says they already know this plant typed its name as: "
    # plant_name is interpolated right after this prefix — see
    # identify_plant_by_name below.
)

IDENTIFY_BY_NAME_PROMPT_SUFFIX = (
    ' First judge whether this genuinely names a real, living plant species — a common name, '
    "vernacular/regional name, or scientific name, in any language or spelling the user might "
    "reasonably use — as opposed to gibberish, a joke, or the name of something that plainly isn't "
    "a plant. Respond ONLY with valid JSON, no other text, in this exact "
    'shape: {"is_real_plant": boolean, "fun_message": string, "species": string, "common_name": '
    'string, "regional_names": array of up to 4 strings, "confidence": "high"|"medium"|"low", '
    '"water_frequency_days": number, "light_needs": string (max 4 words), "care_note": string '
    '(max 20 words), "soil_type": string (max 4 words), "soil_amendments": string (max 20 words), '
    '"fun_facts": array of 4 strings, each max 25 words, genuinely interesting '
    'and specific to this species (not generic plant-care tips), '
    '"is_indoor": boolean (true if commonly grown as a houseplant, false if primarily outdoor/garden), '
    '"is_pet_safe": boolean (true only if non-toxic to cats and dogs if ingested — err toward false if uncertain), '
    '"is_air_purifying": boolean (true if scientifically recognized for measurable air-purifying effect, '
    'not just marketing claims), '
    '"care_difficulty": "easy"|"moderate"|"hard" (based on how forgiving the plant is of missed '
    'watering, variable light, and general neglect)}. '
    'confidence here reflects how sure you are which exact species the user meant (a specific '
    'cultivar name should be "high"; a broad/ambiguous common name shared by several species, e.g. '
    '"cactus" or "fern", should be "low" and you should pick the single most common species people '
    'mean by that name). '
    'Every free-text field (fun_message, care_note, soil_amendments, fun_facts) must be written for a '
    'total beginner with zero botany background — plain everyday words, no unexplained Latin or '
    'technical jargon, nothing generic ("water regularly") — someone who has never grown a plant before '
    'should immediately understand every sentence. '
    'regional_names: this app\'s users are mostly in India — list the common household/vernacular '
    'names this plant actually goes by there (Hindi and other widely-used regional names), e.g. '
    '"Money Plant"/"Paisa Paudha" for Epipremnum aureum, "Tulsi" for holy basil, "Kadi Patta" for '
    'curry leaf, "Ghritkumari" for Aloe vera. Give real, commonly-used names only — if this species '
    'genuinely has no distinct household name in India, return an empty array rather than inventing '
    'one. common_name/species stay in English/Latin as usual; regional_names is purely additive. '
    'soil_type: name an actual soil type this plant thrives in, the way a home gardener in India '
    'would shop for or describe it — e.g. "Red soil", "Black soil", "Sandy loam", "Laterite soil", '
    '"Alluvial soil", or (for a plant normally grown in a pot rather than the ground) "Well-draining '
    'potting mix". Pick whichever term is actually the best real answer for this species\' natural '
    'preference — not a vague description, a named type. '
    'soil_amendments: what to mix INTO that base soil_type for this species to grow at its best — '
    'e.g. "Add compost, sand and cocopeat for drainage and moisture retention" or "Mix in perlite and '
    'organic manure". Practical and specific to this plant\'s actual needs (drainage, moisture '
    'retention, nutrients, pH), not generic filler. '
    'If is_real_plant is false: set fun_message to one short, warm, playful sentence (max 30 words) '
    'reacting specifically to what the user typed — never scold or sound like an error message. Every '
    'other field still needs a real value (species/common_name can name what it looks like; use '
    'reasonable defaults for the rest, regional_names can be empty) since the app always expects '
    'them, but the client only shows fun_message to the user in this case. '
    'If is_real_plant is true but the name is too vague/ambiguous to name one exact species with '
    'reasonable confidence, set confidence to "low", leave fun_message as an empty string, and give '
    'your best general guess, but still provide your best-effort values for every other field.'
)


async def identify_plant_by_name(plant_name: str) -> Dict[str, Any]:
    """Text-only sibling of identify_plant — no image, no vision call. See
    schemas/ai.py's IdentifyByNameRequest for why this exists and shares
    identify's ai_actions cost."""
    try:
        client = _get_client()
        response = await client.aio.models.generate_content(
            model=settings.ai_model,
            contents=[IDENTIFY_BY_NAME_PROMPT_PREFIX + json.dumps(plant_name) + IDENTIFY_BY_NAME_PROMPT_SUFFIX],
        )
        return _parse_json_response(response.text)
    except APIError as exc:
        raise ExternalProviderError(f"AI provider could not look up the plant: {exc}") from exc
    except Exception as exc:
        raise ExternalProviderError(f"Unexpected error calling AI provider: {exc}") from exc


def _diagnose_prompt(species: str) -> str:
    return (
        f'This is a {species}. First image is the whole plant, second is a close-up of the '
        'problem area. Diagnose what\'s wrong. Respond ONLY with valid JSON: '
        '{"confidence": "high"|"medium"|"low", "likely_causes": [string, string] (max 2, ranked '
        'most to least likely), "recommended_action": string (max 30 words), "urgency": '
        '"low"|"medium"|"high"}. Never give a single overconfident answer when symptoms are '
        'ambiguous — list the top 2 causes if uncertain. Write likely_causes and recommended_action '
        'for a total beginner with zero botany background — plain everyday words, no unexplained '
        'Latin or technical jargon (say "yellowing leaves from too much water" not "chlorosis from '
        'overwatering").'
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
        "Write watering_reasoning, fertilizer_reasoning, and light_fit_reasoning for a total beginner "
        "with zero botany background — plain everyday words, no unexplained Latin or technical jargon. "
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
            # Unlike identify/diagnose (open-ended, one-shot judgment calls
            # where some variation in wording is fine), this result is a
            # concrete number a user checks against their own watering
            # calendar — the same plant with the same season/pot/room-light
            # inputs should get the same answer every time, not a different
            # one depending on the model's default sampling temperature
            # (which defaults to ~1.0, i.e. deliberately varied). temperature=0
            # asks for the model's single most-likely output instead of a
            # sampled one, which is as close to deterministic as an LLM call
            # gets (still not a byte-for-byte guarantee across all inputs/
            # model versions, but eliminates the "10 days one run, 8 the
            # next" variance for genuinely identical inputs in practice).
            config=types.GenerateContentConfig(temperature=0),
        )
        return _parse_json_response(response.text)
    except APIError as exc:
        raise ExternalProviderError(f"AI provider could not compute care calculators: {exc}") from exc
    except Exception as exc:
        raise ExternalProviderError(f"Unexpected error calling AI provider: {exc}") from exc
