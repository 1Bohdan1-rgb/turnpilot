"""The interface's language: Ukrainian (the default) or English, one dictionary.

The key is the English text; `UK` gives its Ukrainian. Templates and routes call `_("text", name=value)`: the text is
looked up first, then formatted with `str.format`, so a placeholder is `{name}`. A text missing from `UK` is shown in
English (tests/test_i18n.py checks that every text the templates and routes ask for is here).

Not translated on purpose: what the code generates (the planner's notes and warnings, the simulation's errors, the
services' error messages), and the G-code (ASCII).

Glossary: job = завдання (not "деталь"), holder = державка, facing = торцювання, turret = револьверна головка,
insert = пластина, roughing / finishing = чорнова / чистова, grooving = канавка, parting = відрізання, threading =
нарізання різьби, blank = заготовка, stick-out = виліт, process sheet = технологічний лист, order = замовлення,
approve = затвердити, ready to run = готово до запуску, cutting data = режими різання, operator = оператор.
"""
from flask import has_request_context, request
from markupsafe import Markup

LANGUAGES = ("uk", "en")
DEFAULT_LANGUAGE = "uk"
COOKIE = "lang"


def current_language():
    if not has_request_context():
        return DEFAULT_LANGUAGE
    lang = request.cookies.get(COOKIE)
    return lang if lang in LANGUAGES else DEFAULT_LANGUAGE


def gettext(text, **values):
    out = UK.get(text, text) if current_language() == "uk" else text
    return out.format(**values) if values else out


_ = gettext


def template_gettext(text, **values):
    """As `gettext`, for templates: the text is ours, written into the page as it is (as it was in the template); the
    values are escaped unless they are markup (`link(...)`, `strong(...)`)."""
    out = Markup(UK.get(text, text) if current_language() == "uk" else text)
    return out.format(**values) if values else out


def link(href, text):
    """A link to put into a translated sentence as a value."""
    return Markup('<a href="{}">{}</a>').format(href, text)


def strong(text):
    return Markup("<strong>{}</strong>").format(text)


def label(value):
    """A stored code shown to a person (a status, a tool type, a feature type): in Ukrainian its words, in English
    the code itself, as before."""
    if value is None:
        return ""
    return LABELS.get(value, value) if current_language() == "uk" else value


def js_strings():
    """The texts the pages' scripts ask for (static/*.js, inline scripts), in the current language."""
    return {key: gettext(key) for key in JS_KEYS}


# texts used by scripts (looked up with t("…") in the browser)
JS_KEYS = (
    "Running", "Setup", "Stopped", "Free", "Reading…",
)

UK = {
    # top bar
    "Shop": "Цех",
    "Machines": "Верстати",
    "Cutting data": "Режими різання",
    "Jobs": "Завдання",
    "Upload drawing": "Завантажити креслення",
    "Language": "Мова",
    "Light or dark theme": "Світла або темна тема",
    "Theme": "Тема",
    # machine statuses
    "Running": "Працює",
    "Setup": "Налагодження",
    "Stopped": "Зупинений",
    "Free": "Вільний",
    # jobs.html
    "New job": "Нове завдання",
    "Enter the job by hand, or {link}.": "Введіть завдання вручну або {link}.",
    "create it from a drawing": "створіть його з креслення",
    "Name": "Назва",
    "Material": "Матеріал",
    "Quantity": "Кількість",
    "Blank shape": "Форма заготовки",
    "Blank Ø (hex bar: S across flats), mm": "Ø заготовки (шестигранник: S під ключ), мм",
    "Blank length, mm": "Довжина заготовки, мм",
    "Create job": "Створити завдання",
    "Existing jobs": "Наявні завдання",
    "Qty": "К-сть",
    "Blank × L": "Заготовка × L",
    "Features": "Елементи",
    "Operations": "Операції",
    "No jobs yet.": "Завдань ще немає.",
    # upload.html, machines.html
    "New job from a drawing": "Нове завдання з креслення",
    "Upload a PNG, JPG or PDF drawing (up to {mb} MB).": "Завантажте креслення PNG, JPG або PDF (до {mb} МБ).",
    "Claude reads it, then you check every value before the job is created.": "Claude його читає, потім ви перевіряєте кожне значення, перш ніж завдання буде створено.",
    "For a PDF only the first page is read.": "У PDF читається лише перша сторінка.",
    "The new job will be planned for {machine} (from the shop page); the job page can change it.": "Нове завдання буде сплановано для {machine} (зі сторінки цеху); на сторінці завдання це можна змінити.",
    "Read drawing": "Прочитати креслення",
    "Reading takes 3–7 minutes. A file already read by the same model is not sent again.": "Читання триває 3–7 хвилин. Файл, уже прочитаний тією самою моделлю, повторно не надсилається.",
    "Reading…": "Читаю…",
    "Recent uploads": "Останні завантаження",
    "Uploaded (UTC)": "Завантажено (UTC)",
    "File": "Файл",
    "Model": "Модель",
    "Status": "Стан",
    "DXF, read by the code": "DXF, прочитано програмою",
    "part {n}": "деталь {n}",
    "cached": "з кешу",
    "Review": "Перевірити",
    "Job": "Завдання",
    "Machine": "Верстат",
    "Max rpm": "Макс. об/хв",
    "Power S1, kW": "Потужність S1, кВт",
    "Max Ø, mm": "Макс. Ø, мм",
    "Turret": "Револьверна головка",
    "Passport": "Паспорт",
    "{n} positions, {k} with a tool": "{n} позицій, з інструментом {k}",
    "passport": "паспорт",
    "Add a machine": "Додати верстат",
    "No value is filled in for you: type them from the machine's plate or passport. The new machine's turret is empty, its programming values and passport too; the tool library, materials and cutting data are shared by all machines.": "Жодне значення не заповнюється за вас: введіть їх з таблички верстата або паспорта. Револьверна головка нового верстата порожня, його значення програмування й паспорт теж; бібліотека інструментів, матеріали й режими різання спільні для всіх верстатів.",
    "Add the machine": "Додати верстат",
}


# stored codes shown as words (the `label` filter); in English the code is shown as it is
LABELS = {
    # feature types
    "face": "торець", "od_turn": "зовнішній діаметр", "groove": "канавка", "thread": "різьба", "bore": "отвір",
    "chamfer": "фаска", "parting": "відрізання", "taper": "конус", "fillet": "галтель", "hex": "шестигранник",
    "arc": "дуга",
    # tool types
    "facing": "торцювання", "turning_rough": "чорнове точіння", "turning_finish": "чистове точіння",
    "grooving": "канавка", "threading": "нарізання різьби", "boring": "розточування", "drilling": "свердління",
    "tapping": "нарізання різьби мітчиком", "threading_internal": "нарізання внутрішньої різьби",
    "milling": "фрезерування", "centre_drilling": "центрування", "boring_rough": "чорнове розточування",
    # rough / finish
    "rough": "чорнова", "finish": "чистова",
    # operation statuses
    "proposed": "запропоновано", "approved": "затверджено", "edited": "змінено", "skipped": "пропущено",
    # drawing readings, documents, cutting data rows, programs, orders
    "pending": "очікує", "uploaded": "завантажено", "extracted": "прочитано", "failed": "помилка",
    "confirmed": "підтверджено", "read": "прочитано", "rejected": "відхилено", "replaced": "замінено",
    "simulated": "змодельовано", "ready": "готово до запуску", "open": "відкрите", "closed": "закрите",
    # machine statuses
    "free": "вільний", "setting up": "налагодження", "running": "працює", "stopped": "зупинений", "alarm": "аварія",
    # blank shapes
    "round": "круг", "hex": "шестигранник",
}
