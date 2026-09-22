"""Message templates and the 24-hour reply window on the WhatsApp API line.

A business can write to a person only inside 24 hours of that person's
last message. Outside that window the only thing that goes through is a
template approved beforehand. Templates belong to the business's WhatsApp
account on the messaging provider, so they are read and created there,
never stored here: the provider's answer is the truth about their status.

A template is a header (text, media sample or location), a body, a footer
and up to ten buttons. Variables are written ``{{name}}``; the older
``{{1}}``, ``{{2}}`` form is also understood when reading, but a template
uses one form only. Every variable needs an example value, because the
message is reviewed as a person would read it.
"""

from __future__ import annotations

import re
from datetime import timedelta

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Client, WhatsAppCloudChannel, now_utc
from . import messaging_provider as provider


REPLY_WINDOW_HOURS = 24
TEMPLATE_CATEGORIES = ("UTILITY", "MARKETING")
HEADER_FORMATS = ("TEXT", "IMAGE", "VIDEO", "DOCUMENT", "LOCATION")
MEDIA_HEADER_FORMATS = ("IMAGE", "VIDEO", "DOCUMENT")
BUTTON_TYPES = ("QUICK_REPLY", "URL", "PHONE_NUMBER", "COPY_CODE")
# What WhatsApp accepts as a header sample, and the mime it expects for each.
SAMPLE_MIME_TYPES = {
    "IMAGE": ("image/jpeg", "image/png"),
    "VIDEO": ("video/mp4",),
    "DOCUMENT": ("application/pdf",),
}

# Limits WhatsApp enforces on each part; the same numbers the editor shows.
MAX_HEADER_TEXT = 60
MAX_BODY_TEXT = 1024
MAX_FOOTER_TEXT = 60
MAX_BUTTONS = 10
MAX_BUTTON_TEXT = 25
MAX_URL = 2000
MAX_PHONE = 20
MAX_COPY_CODE = 15
MAX_URL_BUTTONS = 2

# The language codes WhatsApp accepts for a template.
LANGUAGES = frozenset({
    "af", "sq", "ar", "ar_EG", "ar_AE", "ar_LB", "ar_MA", "ar_QA", "az", "be_BY", "bn", "bn_IN", "bg", "ca", "zh_CN",
    "zh_HK", "zh_TW", "hr", "cs", "da", "prs_AF", "nl", "nl_BE", "en", "en_GB", "en_US", "en_AE", "en_AU", "en_CA",
    "en_GH", "en_IE", "en_IN", "en_JM", "en_MY", "en_NZ", "en_QA", "en_SG", "en_UG", "en_ZA", "et", "fil", "fi", "fr",
    "fr_BE", "fr_CA", "fr_CH", "fr_CI", "fr_MA", "ka", "de", "de_AT", "de_CH", "el", "gu", "ha", "he", "hi", "hu", "id",
    "ga", "it", "ja", "kn", "kk", "rw_RW", "ko", "ky_KG", "lo", "lv", "lt", "mk", "ms", "ml", "mr", "nb", "ps_AF", "fa",
    "pl", "pt_BR", "pt_PT", "pa", "ro", "ru", "sr", "si_LK", "sk", "sl", "es", "es_AR", "es_CL", "es_CO", "es_CR",
    "es_DO", "es_EC", "es_HN", "es_MX", "es_PA", "es_PE", "es_ES", "es_UY", "sw", "sv", "ta", "te", "th", "tr", "uk",
    "ur", "uz", "vi", "zu",
})

_NAME = re.compile(r"^[a-z0-9_]{1,512}$")
# A variable as WhatsApp writes it: a number, or a name in lowercase letters and
# underscores. Anything else between double braces is a mistake to report.
_VARIABLE = re.compile(r"\{\{([a-z_]+|[0-9]+)\}\}")
_BRACES = re.compile(r"\{\{.*?\}\}|\{\{|\}\}")
_PHONE = re.compile(r"^\+?[0-9]{5,19}$")


def window_open_until(last_inbound_at: datetime | None) -> datetime | None:
    """When free-form replies stop being allowed, or None if the contact
    never wrote (then nothing but a template was ever allowed)."""
    if last_inbound_at is None:
        return None
    return last_inbound_at + timedelta(hours=REPLY_WINDOW_HOURS)


def window_is_open(last_inbound_at: datetime | None) -> bool:
    until = window_open_until(last_inbound_at)
    return bool(until and until > now_utc())


def template_account(db: Session, client: Client, channel: WhatsAppCloudChannel | None = None) -> str:
    """The provider account id templates are managed with, from the client's
    WhatsApp API numbers. Both the portal and the agency go through here.
    Without ``channel`` the client's first connected number is used."""
    if channel is not None:
        if not channel.external_account_id:
            raise HTTPException(
                status_code=409,
                detail="Connect this WhatsApp API number before managing templates",
            )
        return channel.external_account_id
    row = db.scalar(
        select(WhatsAppCloudChannel).where(
            WhatsAppCloudChannel.client_id == client.id,
            WhatsAppCloudChannel.external_account_id != "",
        ).order_by(WhatsAppCloudChannel.is_enabled.desc(), WhatsAppCloudChannel.created_at).limit(1)
    )
    if not row:
        raise HTTPException(
            status_code=409,
            detail="Templates need a connected WhatsApp API number",
        )
    return row.external_account_id


def parameters(text: str) -> list[str]:
    """The variables of a text, each once, in order of first appearance."""
    seen: list[str] = []
    for name in _VARIABLE.findall(text or ""):
        if name not in seen:
            seen.append(name)
    return seen


def variable_count(body: str) -> int:
    return len(parameters(body))


def parameter_format(names: list[str]) -> str:
    """NAMED or POSITIONAL, from the variables a template uses."""
    return "POSITIONAL" if names and all(n.isdigit() for n in names) else "NAMED"


def validate_template_name(name: str) -> str:
    name = (name or "").strip()
    if not _NAME.match(name):
        raise HTTPException(status_code=422, detail="Template names use lowercase letters, digits and underscores only")
    return name


def _reject(detail: str) -> HTTPException:
    return HTTPException(status_code=422, detail=detail)


def _check_variables(text: str, *, part: str) -> list[str]:
    """The variables of a part, refusing anything WhatsApp would bounce."""
    names = parameters(text)
    if len(_BRACES.findall(text)) != len(_VARIABLE.findall(text)):
        raise _reject(f"Variables in the {part} are written {{{{name}}}} with lowercase letters and underscores")
    if names and any(n.isdigit() for n in names) and not all(n.isdigit() for n in names):
        raise _reject(f"The {part} mixes named and numbered variables; use one form")
    if names and all(n.isdigit() for n in names):
        expected = [str(i) for i in range(1, len(names) + 1)]
        if sorted(names, key=int) != expected:
            raise _reject(f"Numbered variables in the {part} must run {{{{1}}}}, {{{{2}}}}… without gaps")
    return names


def _example_for(examples: dict[str, str], name: str, *, part: str) -> str:
    value = (examples.get(name) or "").strip()
    if not value:
        raise _reject(f"Give an example value for {{{{{name}}}}} in the {part}")
    return value


def _example_component(names: list[str], examples: dict[str, str], *, part: str, key: str) -> dict:
    """The ``example`` block WhatsApp wants for a part with variables, in the
    shape its parameter format asks for."""
    values = [_example_for(examples, n, part=part) for n in names]
    if parameter_format(names) == "POSITIONAL":
        return {key: [values] if key == "body_text" else values}
    return {f"{key}_named_params": [{"param_name": n, "example": v} for n, v in zip(names, values)]}


def build_components(
    *,
    header: dict | None,
    body: str,
    footer: str,
    buttons: list[dict],
    examples: dict[str, str],
) -> tuple[list[dict], str]:
    """Validate every part the way WhatsApp will and return the components of the
    creation payload plus the parameter format they use. The errors name the
    part and the rule, so the editor can show them next to the field."""
    body = (body or "").strip()
    if not body:
        raise _reject("Write the message")
    if len(body) > MAX_BODY_TEXT:
        raise _reject(f"The message is longer than {MAX_BODY_TEXT} characters")
    body_names = _check_variables(body, part="message")
    if body_names and (_VARIABLE.match(body) or re.search(r"\}\}$", body)):
        raise _reject("The message cannot start or end with a variable; add some text around it")

    components: list[dict] = []
    header_names: list[str] = []
    header = header or {}
    fmt = (header.get("format") or "NONE").upper()
    if fmt == "TEXT":
        text = (header.get("text") or "").strip()
        if not text:
            raise _reject("Write the header text or remove the header")
        if len(text) > MAX_HEADER_TEXT:
            raise _reject(f"The header is longer than {MAX_HEADER_TEXT} characters")
        header_names = _check_variables(text, part="header")
        if len(header_names) > 1:
            raise _reject("The header takes one variable at most")
        component: dict = {"type": "HEADER", "format": "TEXT", "text": text}
        if header_names:
            component["example"] = _example_component(header_names, examples, part="header", key="header_text")
        components.append(component)
    elif fmt in MEDIA_HEADER_FORMATS:
        handle = (header.get("handle") or "").strip()
        if not handle:
            raise _reject("Upload a sample file for the header")
        components.append({"type": "HEADER", "format": fmt, "example": {"header_handle": [handle]}})
    elif fmt == "LOCATION":
        components.append({"type": "HEADER", "format": "LOCATION"})
    elif fmt != "NONE":
        raise _reject("Unknown header type")

    all_names = header_names + body_names
    if all_names and len({parameter_format([n]) for n in all_names}) > 1:
        raise _reject("The header and the message must use the same kind of variables")
    fmt_used = parameter_format(all_names)

    body_component: dict = {"type": "BODY", "text": body}
    if body_names:
        body_component["example"] = _example_component(body_names, examples, part="message", key="body_text")
    components.append(body_component)

    footer = (footer or "").strip()
    if footer:
        if len(footer) > MAX_FOOTER_TEXT:
            raise _reject(f"The footer is longer than {MAX_FOOTER_TEXT} characters")
        if _VARIABLE.search(footer):
            raise _reject("The footer cannot have variables")
        components.append({"type": "FOOTER", "text": footer})

    if buttons:
        components.append({"type": "BUTTONS", "buttons": _check_buttons(buttons)})
    return components, fmt_used


def _check_buttons(buttons: list[dict]) -> list[dict]:
    if len(buttons) > MAX_BUTTONS:
        raise _reject(f"A template takes {MAX_BUTTONS} buttons at most")
    out: list[dict] = []
    counts = {kind: 0 for kind in BUTTON_TYPES}
    for button in buttons:
        kind = (button.get("type") or "").upper()
        if kind not in BUTTON_TYPES:
            raise _reject("Unknown button type")
        counts[kind] += 1
        text = (button.get("text") or "").strip()
        if kind != "COPY_CODE":
            if not text:
                raise _reject("Every button needs a label")
            if len(text) > MAX_BUTTON_TEXT:
                raise _reject(f"Button labels take {MAX_BUTTON_TEXT} characters at most")
        if kind == "QUICK_REPLY":
            out.append({"type": "QUICK_REPLY", "text": text})
        elif kind == "URL":
            url = (button.get("url") or "").strip()
            if not url.lower().startswith(("http://", "https://")):
                raise _reject("Website buttons need a full address starting with https://")
            if len(url) > MAX_URL:
                raise _reject(f"Website addresses take {MAX_URL} characters at most")
            variables = _VARIABLE.findall(url)
            if len(variables) > 1 or (variables and not url.endswith("{{1}}")):
                raise _reject("A website address can end with {{1}} to add a value per person, nothing else")
            entry: dict = {"type": "URL", "text": text, "url": url}
            if variables:
                example = (button.get("example") or "").strip()
                if not example:
                    raise _reject("Give an example of the value added to the website address")
                entry["example"] = [example]
            out.append(entry)
        elif kind == "PHONE_NUMBER":
            phone = re.sub(r"[\s().-]", "", button.get("phone_number") or "")
            if not _PHONE.match(phone) or len(phone) > MAX_PHONE:
                raise _reject("Call buttons need a phone number with its country code")
            out.append({"type": "PHONE_NUMBER", "text": text, "phone_number": phone})
        elif kind == "COPY_CODE":
            code = (button.get("example") or "").strip()
            if not code or len(code) > MAX_COPY_CODE:
                raise _reject(f"Copy code buttons need an example code of up to {MAX_COPY_CODE} characters")
            out.append({"type": "COPY_CODE", "example": code})
    if counts["URL"] > MAX_URL_BUTTONS:
        raise _reject(f"A template takes {MAX_URL_BUTTONS} website buttons at most")
    if counts["PHONE_NUMBER"] > 1:
        raise _reject("A template takes one call button")
    if counts["COPY_CODE"] > 1:
        raise _reject("A template takes one copy code button")
    # WhatsApp groups the quick replies: they sit together, before or after the rest.
    quick = [i for i, b in enumerate(out) if b["type"] == "QUICK_REPLY"]
    if quick and (quick[-1] - quick[0] + 1 != len(quick) or (quick[0] != 0 and quick[-1] != len(out) - 1)):
        raise _reject("Quick reply buttons go together, before or after the other buttons")
    return out


def normalize(raw: dict) -> dict:
    """The parts of a WhatsApp template the portal shows and sends."""
    body = footer = ""
    header: dict | None = None
    buttons: list[dict] = []
    for component in raw.get("components") or []:
        kind = (component.get("type") or "").upper()
        if kind == "BODY":
            body = component.get("text") or ""
        elif kind == "FOOTER":
            footer = component.get("text") or ""
        elif kind == "HEADER":
            fmt = (component.get("format") or "TEXT").upper()
            text = component.get("text") or "" if fmt == "TEXT" else ""
            header = {"format": fmt, "text": text, "parameters": parameters(text)}
        elif kind == "BUTTONS":
            for button in component.get("buttons") or []:
                btype = (button.get("type") or "").upper()
                url = button.get("url") or ""
                example = button.get("example")
                if isinstance(example, list):
                    example = example[0] if example else ""
                buttons.append({
                    "type": btype,
                    "text": button.get("text") or "",
                    "url": url,
                    "phone_number": button.get("phone_number") or "",
                    "example": example or "",
                    "dynamic": btype == "COPY_CODE" or (btype == "URL" and bool(_VARIABLE.search(url))),
                })
    names = parameters(body)
    # WhatsApp spells "no reason" as the string NONE.
    reason = raw.get("rejected_reason") or None
    if reason and reason.upper() == "NONE":
        reason = None
    return {
        "id": raw.get("id"),
        "name": raw.get("name") or "",
        "language": raw.get("language") or "",
        "category": (raw.get("category") or "").upper(),
        "status": (raw.get("status") or "").upper(),
        "parameter_format": (raw.get("parameter_format") or parameter_format(names + (header or {}).get("parameters", []))).upper(),
        "header": header,
        "body": body,
        "footer": footer,
        "buttons": buttons,
        "parameters": names,
        "variables": len(names),
        "rejected_reason": reason,
    }


def render(text: str, values: dict[str, str]) -> str:
    """The text the person will read, with the variables filled in."""
    return _VARIABLE.sub(lambda m: values.get(m.group(1)) or m.group(0), text or "")


def rendered_text(template: dict, *, body_values: list[str], header_value: str = "") -> str:
    """What the portal keeps as the message: header, body and footer as read."""
    values = dict(zip(template["parameters"], body_values))
    parts = []
    header = template.get("header")
    if header and header["format"] == "TEXT":
        parts.append(render(header["text"], dict(zip(header["parameters"], [header_value]))))
    parts.append(render(template["body"], values))
    if template.get("footer"):
        parts.append(template["footer"])
    return "\n\n".join(parts)


def send_components(
    template: dict,
    *,
    body_values: list[str],
    header_value: str = "",
    location: dict | None = None,
    button_values: list[str] | None = None,
) -> list[dict]:
    """The components of a send, with each value in the slot WhatsApp expects.
    Refuses a send that would leave a variable unfilled."""
    named = template["parameter_format"] == "NAMED"
    names = template["parameters"]
    if len(body_values) != len(names) or any(not v.strip() for v in body_values):
        raise _reject(f"This template takes {len(names)} values")

    def text_param(name: str, value: str) -> dict:
        param = {"type": "text", "text": value}
        if named:
            param["parameter_name"] = name
        return param

    components: list[dict] = []
    header = template.get("header")
    if header:
        fmt = header["format"]
        if fmt == "TEXT" and header["parameters"]:
            if not header_value.strip():
                raise _reject("This template needs a value for the header")
            components.append({"type": "header", "parameters": [text_param(header["parameters"][0], header_value)]})
        elif fmt in MEDIA_HEADER_FORMATS:
            link = header_value.strip()
            if not link.lower().startswith("https://"):
                raise _reject("This template needs a public https:// link to the header file")
            key = fmt.lower()
            components.append({"type": "header", "parameters": [{"type": key, key: {"link": link}}]})
        elif fmt == "LOCATION":
            if not location or location.get("latitude") is None or location.get("longitude") is None:
                raise _reject("This template needs a location for the header")
            components.append({"type": "header", "parameters": [{"type": "location", "location": {
                "latitude": str(location["latitude"]), "longitude": str(location["longitude"]),
                "name": location.get("name") or "", "address": location.get("address") or "",
            }}]})
    if names:
        components.append({"type": "body", "parameters": [text_param(n, v) for n, v in zip(names, body_values)]})
    button_values = button_values or []
    for index, button in enumerate(template.get("buttons") or []):
        if not button.get("dynamic"):
            continue
        value = button_values[index].strip() if index < len(button_values) else ""
        if not value:
            raise _reject("This template needs a value for one of its buttons")
        if button["type"] == "URL":
            components.append({"type": "button", "sub_type": "url", "index": index, "parameters": [{"type": "text", "text": value}]})
        elif button["type"] == "COPY_CODE":
            components.append({"type": "button", "sub_type": "copy_code", "index": index, "parameters": [{"type": "coupon_code", "coupon_code": value}]})
    return components


async def list_templates(account_id: str) -> list[dict]:
    return [normalize(item) for item in await provider.list_templates(account_id)]


async def create_template(
    account_id: str,
    *,
    name: str,
    language: str,
    category: str,
    body: str,
    header: dict | None = None,
    footer: str = "",
    buttons: list[dict] | None = None,
    examples: dict[str, str] | None = None,
) -> dict:
    """Submit a template for approval. The review may file it under the other
    category when it reads differently; letting it do so beats a rejection."""
    if language not in LANGUAGES:
        raise _reject("Pick a language WhatsApp supports for templates")
    components, fmt = build_components(header=header, body=body, footer=footer, buttons=buttons or [], examples=examples or {})
    created = await provider.create_template(
        account_id,
        {"name": name, "language": language, "category": category,
         "parameter_format": fmt, "components": components},
    )
    return normalize({
        "id": created.get("id"),
        "name": created.get("name") or name,
        "status": created.get("status") or "PENDING",
        "category": created.get("category") or category,
        "language": created.get("language") or language,
        "parameter_format": fmt,
        "components": components,
    })


async def read_sample(file) -> tuple[bytes, str, str]:
    """The bytes, mime and a safe name of an uploaded header sample, refusing
    what would not pass review."""
    from .messaging_media import SAMPLE_MAX_BYTES

    mime = (file.content_type or "").lower()
    if mime == "image/jpg":
        mime = "image/jpeg"
    if mime not in {m for mimes in SAMPLE_MIME_TYPES.values() for m in mimes}:
        raise HTTPException(status_code=415, detail="Header samples are a JPG or PNG image, an MP4 video or a PDF")
    data = await file.read(SAMPLE_MAX_BYTES + 1)
    if len(data) > SAMPLE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="The sample exceeds the 16 MB limit")
    name = re.sub(r"[^\w. -]", "_", (file.filename or "sample").rsplit("/", 1)[-1])[:120] or "sample"
    return data, mime, name


async def upload_sample(account_id: str, *, data: bytes, mime: str, filename: str) -> str:
    """Host a header sample at a public URL and return it. The template
    refers to the sample by this URL, so review sees the same file the
    sends will carry."""
    from . import messaging_media

    _ = account_id
    handle = messaging_media.store_sample(data, mime, filename)
    return messaging_media.sample_url(handle)


async def delete_template(account_id: str, *, name: str, hsm_id: str | None = None) -> None:
    """Retire a template. By name alone every language variant goes; with an
    id only that variant does."""
    variants = [item for item in await provider.list_templates(account_id, name=name)]
    if hsm_id:
        variants = [item for item in variants if str(item.get("id") or "") == hsm_id]
        if not variants:
            raise HTTPException(status_code=404, detail="That template variant no longer exists")
    elif not variants:
        return
    for variant in variants:
        await provider.delete_template(account_id, name, language=variant.get("language") or None)


def flat_template_params(
    template: dict,
    *,
    body_values: list[str],
    header_value: str = "",
    button_values: list[str] | None = None,
) -> tuple[list[str], list[dict], dict | None]:
    """The flat send shape of a template: positional values (text header,
    body, dynamic URL buttons in order), per-send button payloads (copy
    codes), and the media header override when the send carries its own
    file. Validates through the same rules as the component send, so the
    editor's errors stay identical."""
    send_components(template, body_values=body_values, header_value=header_value, button_values=button_values)
    params: list[str] = []
    header = template.get("header") or {}
    if header.get("format") == "TEXT" and header.get("parameters"):
        params.append(header_value.strip())
    params.extend(body_values)
    button_params: list[dict] = []
    media: dict | None = None
    if header.get("format") in MEDIA_HEADER_FORMATS and header_value.strip():
        media = {"link": header_value.strip()}
    for index, button in enumerate(template.get("buttons") or []):
        if not button.get("dynamic"):
            continue
        value = (button_values[index].strip() if button_values and index < len(button_values) else "")
        if button["type"] == "URL":
            params.append(value)
        elif button["type"] == "COPY_CODE":
            button_params.append({"index": index, "subType": "copy_code", "value": value})
    return params, button_params, media


async def send_template(
    account_id: str, conversation_id: str, *, name: str, language: str, components: list[dict]
) -> str | None:
    """Send an approved template inside its conversation (re-engagement after
    the 24-hour window closed). Returns the provider message id."""
    data = await provider.send_message(
        account_id, conversation_id,
        template={"elements": [{"name": name, "language": language, "components": components or []}]},
    )
    return data.get("messageId")


async def open_template_conversation(
    account_id: str, phone: str, *, template: dict, name: str, language: str,
    body_values: list[str], header_value: str = "", location: dict | None = None,
    button_values: list[str] | None = None,
) -> dict:
    """Send an approved template to a phone number with no open thread. Media
    headers fall back to the approved sample unless the send carries its own
    file; a location header needs its pin now. Returns the provider
    conversation payload."""
    params, button_params, media = flat_template_params(
        template, body_values=body_values, header_value=header_value, button_values=button_values)
    header = template.get("header") or {}
    header_location = None
    if header.get("format") == "LOCATION":
        if not location or location.get("latitude") is None or location.get("longitude") is None:
            raise _reject("This template needs a location for the header")
        header_location = {
            "latitude": float(location["latitude"]), "longitude": float(location["longitude"]),
            "name": location.get("name") or "", "address": location.get("address") or "",
        }
    digits = "".join(char for char in (phone or "") if char.isdigit())
    if not digits:
        raise HTTPException(status_code=409, detail="This conversation does not have a valid WhatsApp destination")
    return await provider.create_conversation(
        account_id, digits, template_name=name, template_language=language,
        template_params=params, template_button_params=button_params or None,
        header_media=media, header_location=header_location)
