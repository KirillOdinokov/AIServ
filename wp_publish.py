"""Публикация изображений на WordPress / WooCommerce через REST API.

Одна папка может содержать изображения для разных типов объектов:
  - товары WooCommerce   (ID.webp)            → главное или доп. изображение
  - категории товаров    (ID.webp)            → миниатюра категории
  - записи (посты)       (ID.webp)            → featured + вставка в текст
  - рубрики              (ID.webp)            → миниатюра рубрики
  - доп. изображения     (ID-N.webp)          → ещё одно изображение товара ID

Тип объекта определяется автоматически по ID: скрипт пробует найти объект
среди товаров, категорий товаров, записей и рубрик.

Аутентификация:
  - WordPress Application Password (Basic Auth) для wp/v2
  - WooCommerce REST API (consumer_key / consumer_secret) для wc/v3
"""
import base64
import re

import requests

# ─── Транслитерация и slug ───────────────────────────────────────────────────

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "А": "A", "Б": "B", "В": "V", "Г": "G", "Д": "D", "Е": "E", "Ё": "E",
    "Ж": "Zh", "З": "Z", "И": "I", "Й": "Y", "К": "K", "Л": "L", "М": "M",
    "Н": "N", "О": "O", "П": "P", "Р": "R", "С": "S", "Т": "T", "У": "U",
    "Ф": "F", "Х": "H", "Ц": "Ts", "Ч": "Ch", "Ш": "Sh", "Щ": "Sch",
    "Ъ": "", "Ы": "Y", "Ь": "", "Э": "E", "Ю": "Yu", "Я": "Ya",
}


def translit(text):
    return "".join(_TRANSLIT.get(ch, ch) for ch in text)


def slugify(text):
    """Преобразовать название в slug (латиница, дефисы)."""
    text = translit(text or "")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    return text[:100] or "image"


def parse_image_name(name):
    """Разобрать имя файла. Вернуть (base_id, suffix).

    '12345.webp'      → ('12345', None)
    '12345-1.webp'    → ('12345', '1')
    '12345-2.webp'    → ('12345', '2')
    's_12345.webp'    → ('12345', None)   # префиксы отбрасываются
    """
    m = re.match(r"^(\d+)(?:-(\d+))?\.[a-z0-9]+$", name, re.I)
    if m:
        return m.group(1), m.group(2)
    # fallback: извлечь первую цифровую группу из имени (на случай префиксов s_, sku_ и т.п.)
    m2 = re.search(r"(\d+)", name)
    if m2:
        return m2.group(1), None
    return None, None


# ─── Клиент REST API ─────────────────────────────────────────────────────────

def _request(method, url, **kwargs):
    kwargs.setdefault("timeout", 60)
    return requests.request(method, url, **kwargs)


def _normalize_url(site):
    site = (site or "").strip()
    if not site:
        raise ValueError("Адрес сайта не задан")
    if not site.startswith(("http://", "https://")):
        site = "https://" + site
    return site.rstrip("/")


class WpClient:
    """Клиент WordPress/WooCommerce REST API."""

    def __init__(self, site, wp_user=None, wp_app_password=None,
                 wc_key=None, wc_secret=None):
        self.site = _normalize_url(site)
        self.wp_user = wp_user
        self.wp_app_password = wp_app_password
        self.wc_key = wc_key
        self.wc_secret = wc_secret

    # ── базовые запросы ──
    def _wp_auth(self):
        if self.wp_user and self.wp_app_password:
            # WordPress Application Password генерируется с пробелами
            # (abcd efgh ...), но в Basic Auth пробелы недопустимы — убираем.
            app_password = self.wp_app_password.replace(" ", "")
            token = base64.b64encode(
                f"{self.wp_user}:{app_password}".encode()
            ).decode()
            return {"Authorization": f"Basic {token}"}
        return {}

    def _wc_auth(self):
        return {
            "consumer_key": self.wc_key,
            "consumer_secret": self.wc_secret,
        }

    def wp_get(self, path, params=None):
        url = f"{self.site}/wp-json/wp/v2/{path}"
        resp = _request("GET", url, headers=self._wp_auth(), params=params or {})
        resp.raise_for_status()
        return resp.json()

    def wp_post(self, path, data=None, files=None):
        url = f"{self.site}/wp-json/wp/v2/{path}"
        resp = _request("POST", url, headers=self._wp_auth(),
                        data=data, files=files)
        resp.raise_for_status()
        return resp.json()

    def wp_patch(self, path, data):
        """Частичное обновление записи/рубрики."""
        url = f"{self.site}/wp-json/wp/v2/{path}"
        resp = _request("PATCH", url, headers=self._wp_auth(), json=data)
        resp.raise_for_status()
        return resp.json()

    def wc_get(self, path, params=None):
        url = f"{self.site}/wp-json/wc/v3/{path}"
        p = dict(self._wc_auth())
        p.update(params or {})
        resp = _request("GET", url, params=p)
        resp.raise_for_status()
        return resp.json()

    def wc_put(self, path, data):
        url = f"{self.site}/wp-json/wc/v3/{path}"
        resp = _request("PUT", url, params=self._wc_auth(), json=data)
        resp.raise_for_status()
        return resp.json()

    # ── определение типа объекта по ID ──
    def resolve_object(self, obj_id):
        """Определить тип объекта по ID. Вернуть (type, title, data).

        type: product | product_cat | post | category | None
        """
        # 1. товар WooCommerce
        if self.wc_key and self.wc_secret:
            try:
                d = self.wc_get(f"products/{obj_id}")
                return "product", d.get("name", ""), d
            except requests.HTTPError:
                pass
            try:
                d = self.wc_get(f"products/categories/{obj_id}")
                return "product_cat", d.get("name", ""), d
            except requests.HTTPError:
                pass
        # 2. запись
        try:
            d = self.wp_get(f"posts/{obj_id}")
            return "post", d.get("title", {}).get("rendered", ""), d
        except requests.HTTPError:
            pass
        # 3. рубрика
        try:
            d = self.wp_get(f"categories/{obj_id}")
            return "category", d.get("name", ""), d
        except requests.HTTPError:
            pass
        return None, None, None

    # ── загрузка изображения ──
    def upload_media(self, file_bytes, filename, alt_text):
        """Загрузить изображение как media, вернуть {id, source_url}."""
        data = {"alt_text": alt_text, "title": alt_text}
        files = {"file": (filename, file_bytes, "image/webp")}
        return self.wp_post("media", data=data, files=files)

    # ── назначение изображения объекту ──
    def assign_to_product(self, obj_id, media_id, replace_main=False):
        """Товар: если replace_main=True — заменить главное изображение,
        иначе — добавить как дополнительное (в галерею)."""
        product = self.wc_get(f"products/{obj_id}")
        images = product.get("images") or []

        if replace_main and images:
            # заменяем главное (первое) изображение, остальные сохраняем
            new_images = [{"id": media_id}] + images[1:]
        else:
            # добавляем в конец галереи
            new_images = images + [{"id": media_id}]

        self.wc_put(f"products/{obj_id}", {"images": new_images})

    def assign_to_category(self, obj_id, media_id):
        """Рубрика: миниатюра через term meta thumbnail_id."""
        self.wp_patch(f"categories/{obj_id}", {"meta": {"thumbnail_id": media_id}})

    def assign_to_product_cat(self, obj_id, media_id):
        """Категория товара: image = {id}."""
        self.wc_put(f"products/categories/{obj_id}", {"image": {"id": media_id}})

    # ── списки категорий и товаров (для миниатюр категорий) ──
    def list_product_categories(self):
        """Все категории товаров WooCommerce. Возвращает [{id, name, slug, count}]."""
        cats = []
        page = 1
        while True:
            data = self.wc_get("products/categories", params={
                "per_page": 100, "page": page, "orderby": "id", "order": "asc",
            })
            if not data:
                break
            for c in data:
                cats.append({
                    "id": c.get("id"),
                    "name": c.get("name", ""),
                    "slug": c.get("slug", ""),
                    "count": c.get("count", 0),
                })
            if len(data) < 100:
                break
            page += 1
        return cats

    def list_products_in_category(self, category_id, per_page=1):
        """Товары в категории (по умолчанию первый)."""
        return self.wc_get("products", params={
            "category": category_id, "per_page": per_page,
            "orderby": "menu_order", "order": "asc",
        })

    def assign_to_post(self, obj_id, media_id, src_url, alt, insert_in_content=False,
                       img_class=""):
        """Запись: featured image + (опционально) вставка в середину текста."""
        self.wp_patch(f"posts/{obj_id}", {"featured_media": media_id})
        if insert_in_content:
            post = self.wp_get(f"posts/{obj_id}")
            content = post.get("content", {}).get("rendered", "") or \
                post.get("content", {}).get("raw", "")
            if content:
                new_content = self._insert_image_in_content(
                    content, src_url, alt, img_class
                )
                if new_content != content:
                    self.wp_patch(f"posts/{obj_id}", {"content": new_content})

    def _insert_image_in_content(self, content, src_url, alt, img_class):
        """Вставить <figure><img></figure> после абзаца примерно в середине."""
        paragraphs = re.findall(r"<p[^>]*>.*?</p>", content, re.S)
        if not paragraphs:
            return content + self._image_html(src_url, alt, img_class)

        mid = len(paragraphs) // 2
        anchor = paragraphs[mid - 1] if mid > 0 else paragraphs[0]
        img = self._image_html(src_url, alt, img_class)
        return content.replace(anchor, anchor + img, 1)

    def _image_html(self, src_url, alt, img_class):
        cls = f' class="{img_class}"' if img_class else ""
        return (
            f'<figure{cls}>'
            f'<img src="{src_url}" alt="{alt}">'
            f'<figcaption>{alt}</figcaption>'
            f'</figure>'
        )

    def test_connection(self):
        """Проверка подключения: вернуть сводку по доступности."""
        result = {"site": self.site}
        try:
            info = self.wp_get("")
            result["wp"] = {"ok": True, "name": info.get("name", "")}
        except Exception as e:
            result["wp"] = {"ok": False, "error": str(e)}
        if self.wc_key and self.wc_secret:
            try:
                self.wc_get("products", params={"per_page": 1})
                result["wc"] = {"ok": True}
            except Exception as e:
                result["wc"] = {"ok": False, "error": str(e)}
        return result
