from html import escape
from typing import Any


def _paragraph(text: str) -> str:
    return (
        '<p style="font-size:18px;line-height:1.9;color:#333;'
        'margin:14px 0;text-align:justify;">'
        f"{escape(text)}</p>"
    )


def render_wechat_html(
    article: dict[str, Any],
    asset_urls: list[str] | None = None,
    asset_credits: list[dict[str, str | None]] | None = None,
) -> str:
    asset_urls = asset_urls or []
    asset_credits = asset_credits or []
    html: list[str] = [
        (
            '<section style="max-width:680px;margin:0 auto;padding:8px 12px;'
            'font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;">'
        )
    ]
    html.append(_paragraph(article.get("opening", "")))

    inline_index = 1
    for section_index, section in enumerate(article.get("sections", [])):
        if section_index > 0 and inline_index < len(asset_urls):
            html.append(
                '<p style="text-align:center;margin:22px 0;">'
                f'<img src="{escape(asset_urls[inline_index])}" '
                'style="max-width:100%;height:auto;border-radius:8px;" />'
                "</p>"
            )
            inline_index += 1
        html.append(
            '<h2 style="font-size:21px;line-height:1.5;color:#2f6f4e;'
            'margin:28px 0 12px;border-left:4px solid #70a987;padding-left:10px;">'
            f"{escape(section['heading'])}</h2>"
        )
        for paragraph in section.get("paragraphs", []):
            html.append(_paragraph(paragraph))

    html.append(
        '<section style="background:#f4f8f5;border-radius:8px;padding:12px 16px;margin-top:26px;">'
    )
    html.append(_paragraph(article.get("conclusion", "")))
    html.append("</section>")

    web_credits = [item for item in asset_credits if item.get("source_kind") == "web"]
    if web_credits:
        html.append(
            '<section style="font-size:12px;line-height:1.7;color:#8a958e;'
            'margin:18px 0 6px;">'
            "<p>图片来源与许可：</p>"
        )
        for credit in web_credits:
            author = escape(credit.get("source_author") or "Wikimedia Commons")
            source_url = escape(credit.get("source_page_url") or "")
            license_name = escape(credit.get("license_name") or "开放许可")
            license_url = escape(credit.get("license_url") or "")
            author_html = (
                f'<a href="{source_url}" style="color:#708078;">{author}</a>'
                if source_url
                else author
            )
            license_html = (
                f'<a href="{license_url}" style="color:#708078;">{license_name}</a>'
                if license_url
                else license_name
            )
            html.append(f"<p>{author_html} · {license_html}</p>")
        html.append("</section>")

    html.append("</section>")
    return "".join(html)
