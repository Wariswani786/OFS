#!/usr/bin/env python3
"""
PromoPatriot Content Generator
Render-ready web server
"""

import http.server
import json
import os
import ssl
import gzip
import zlib
import re
import html as html_module
import base64
import urllib.request
import urllib.error
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ── Config from environment variables (set in Render dashboard) ────────
WC_HOST            = os.environ.get("WC_HOST", "")
WC_CONSUMER_KEY    = os.environ.get("WC_CONSUMER_KEY", "")
WC_CONSUMER_SECRET = os.environ.get("WC_CONSUMER_SECRET", "")
PRIORITY_USER      = os.environ.get("PRIORITY_USER", "API")
PRIORITY_PASS      = os.environ.get("PRIORITY_PASS", "Aa12345")
WP_ADMIN_USER      = os.environ.get("WP_ADMIN_USER", "")
WP_ADMIN_PASS      = os.environ.get("WP_ADMIN_PASS", "")
PORT               = int(os.environ.get("PORT", 8000))

ssl_ctx = ssl.create_default_context()
ssl_ctx.check_hostname = False
ssl_ctx.verify_mode = ssl.CERT_NONE

def decompress(data, encoding):
    try:
        if encoding == "gzip":
            return gzip.decompress(data)
        elif encoding in ("deflate", "zlib"):
            return zlib.decompress(data)
    except Exception:
        pass
    return data

def make_request(url, data, method, creds):
    req = urllib.request.Request(
        url, data=data,
        headers={
            "Content-Type": "application/json; charset=utf-8",
            "Authorization": f"Basic {creds}",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/125.0.0.0",
            "Accept": "application/json",
            "Origin": f"https://{WC_HOST}",
            "Referer": f"https://{WC_HOST}/wp-admin/",
        },
        method=method
    )
    with urllib.request.urlopen(req, context=ssl_ctx) as resp:
        raw = resp.read()
        return decompress(raw, resp.headers.get("Content-Encoding", "").lower()), resp.status


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        print(f"  {args[0]} {args[1]}")

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        if self.path in ("/", ""):
            self.path = "/woo-content-generator.html"
            super().do_GET()
        elif self.path.startswith("/priority-lookup"):
            self._priority_lookup()
        elif self.path == "/health":
            self._respond(200, json.dumps({"status": "ok", "host": WC_HOST}).encode())
        else:
            super().do_GET()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)

        if self.path == "/woo-update":
            self._woo_update(body)
        elif self.path.startswith("/woo-id-lookup"):
            self._woo_id_lookup()
        else:
            self._respond(404, b'{"error":"not found"}')

    def _woo_update(self, body):
        try:
            payload    = json.loads(body)
            product_id = payload.get("product_id")
            description = html_module.unescape(payload.get("description", ""))
            short_desc  = html_module.unescape(payload.get("short_description", ""))
            seo_data    = payload.get("seo", {})

            creds = base64.b64encode(f"{WC_CONSUMER_KEY}:{WC_CONSUMER_SECRET}".encode()).decode()

            print(f"\n  Product   : #{product_id}")
            print(f"  HTML size : {len(description)} chars")


            # Encode into WPBakery vc_raw_html shortcode (base64)
            import re as _re
            # Split Widget 2 (dark bg) into its own row, rest into another
            w2_match = _re.search(
                r'(<!-- Widget 2:.*?</div>\s*<div style="height:40px;"></div>)(\s*<!-- Widget 1:.*)',
                description, _re.DOTALL
            )
            if w2_match:
                w2_html   = w2_match.group(1).strip()
                rest_html = w2_match.group(2).strip()
                enc_w2    = base64.b64encode(w2_html.encode('utf-8')).decode('utf-8')
                enc_rest  = base64.b64encode(rest_html.encode('utf-8')).decode('utf-8')
                rand_id   = str(abs(hash(w2_html)) % 99999)
                wpb_content = (
                    f'[vc_row css=".vc_custom_{rand_id}{{background-color: #0A1F3F !important;}}"]'
                    f'[vc_column][vc_raw_html]{enc_w2}[/vc_raw_html][/vc_column][/vc_row]'
                    f'[vc_row][vc_column][vc_raw_html]{enc_rest}[/vc_raw_html][/vc_column][/vc_row]'
                )
                print(f"  WPBakery  : W2 dark row + content row")
            else:
                enc = base64.b64encode(description.encode('utf-8')).decode('utf-8')
                wpb_content = f'[vc_row][vc_column][vc_raw_html]{enc}[/vc_raw_html][/vc_column][/vc_row]'
                print(f"  WPBakery  : single block ({len(description)} chars)")

            # ── Build WooCommerce payload ──────────────────────────────────────
            send_payload = {
                "description": wpb_content,
                "short_description": short_desc
            }
            if payload.get("name"):          send_payload["name"]          = payload["name"]
            if payload.get("sku"):           send_payload["sku"]           = payload["sku"]
            if payload.get("regular_price"): send_payload["regular_price"] = str(payload["regular_price"])
            if payload.get("weight"):        send_payload["weight"]        = str(payload["weight"])

            # ── Rank Math SEO via meta_data ────────────────────────────────────
            if seo_data.get("title") or seo_data.get("description") or seo_data.get("focus_keyword"):
                meta_data = []
                if seo_data.get("title"):         meta_data.append({"key": "rank_math_title",         "value": seo_data["title"]})
                if seo_data.get("description"):   meta_data.append({"key": "rank_math_description",   "value": seo_data["description"]})
                if seo_data.get("focus_keyword"): meta_data.append({"key": "rank_math_focus_keyword", "value": seo_data["focus_keyword"]})
                send_payload["meta_data"] = meta_data
                print(f"  SEO       : {[m['key'] for m in meta_data]}")

            send_data = json.dumps(send_payload, ensure_ascii=False).encode("utf-8")
            result, status = make_request(
                f"https://{WC_HOST}/wp-json/wc/v3/products/{product_id}",
                send_data, "PUT", creds
            )
            print(f"  WC status : {status}")

            # Simulate clicking Update in WP admin to trigger WPBakery CSS regeneration
            try:
                import urllib.parse as _up, re as _re, http.cookiejar as _cj
                # Login to WP admin
                _cjar = _cj.CookieJar()
                _opener = urllib.request.build_opener(
                    urllib.request.HTTPCookieProcessor(_cjar),
                    urllib.request.HTTPSHandler(context=ssl_ctx)
                )
                _login = _opener.open(
                    urllib.request.Request(
                        f"https://{WC_HOST}/wp-login.php",
                        data=_up.urlencode({"log": WP_ADMIN_USER or PRIORITY_USER, "pwd": WP_ADMIN_PASS or PRIORITY_PASS,
                            "wp-submit": "Log In", "testcookie": "1",
                            "redirect_to": "/wp-admin/"}).encode(),
                        headers={"Content-Type": "application/x-www-form-urlencoded",
                                 "User-Agent": "Mozilla/5.0",
                                 "Cookie": "wordpress_test_cookie=WP+Cookie+check"},
                        method="POST"
                    )
                )
                print(f"  WP Login  : {_login.status}")
                # Get edit page for nonce
                _edit = _opener.open(f"https://{WC_HOST}/wp-admin/post.php?post={product_id}&action=edit")
                _ehtml = _edit.read().decode("utf-8", errors="ignore")
                _nm = _re.search(r'name="_wpnonce" value="([^"]+)"', _ehtml)
                _nonce = _nm.group(1) if _nm else ""
                print(f"  WP Nonce  : {'found' if _nonce else 'not found'}")
                if _nonce:
                    _save = _opener.open(
                        urllib.request.Request(
                            f"https://{WC_HOST}/wp-admin/post.php",
                            data=_up.urlencode({"post_ID": product_id, "action": "editpost",
                                "post_status": "publish", "post_type": "product",
                                "_wpnonce": _nonce, "save": "Update"}).encode(),
                            headers={"Content-Type": "application/x-www-form-urlencoded",
                                     "User-Agent": "Mozilla/5.0",
                                     "Referer": f"https://{WC_HOST}/wp-admin/post.php?post={product_id}&action=edit"},
                            method="POST"
                        )
                    )
                    print(f"  WP Update : clicked ({_save.status})")
            except Exception as _we:
                print(f"  WP Update : {_we} (non-fatal)")



            # ── Also try WP REST API for Rank Math meta (belt + braces) ───────
            if seo_data:
                try:
                    wc_json = json.loads(result)
                    post_id = wc_json.get("id", product_id)
                    meta_map = {}
                    if seo_data.get("title"):         meta_map["rank_math_title"]         = seo_data["title"]
                    if seo_data.get("description"):   meta_map["rank_math_description"]   = seo_data["description"]
                    if seo_data.get("focus_keyword"): meta_map["rank_math_focus_keyword"] = seo_data["focus_keyword"]

                    wp_req = urllib.request.Request(
                        f"https://{WC_HOST}/wp-json/wp/v2/product/{post_id}",
                        data=json.dumps({"meta": meta_map}, ensure_ascii=False).encode("utf-8"),
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Basic {creds}",
                            "User-Agent": "Mozilla/5.0"
                        },
                        method="POST"
                    )
                    with urllib.request.urlopen(wp_req, context=ssl_ctx) as r:
                        print(f"  Rank Math : {r.status}")
                except Exception as e:
                    print(f"  Rank Math : {e} (non-fatal)")

            self._respond(200, result)

        except urllib.error.HTTPError as e:
            raw = decompress(e.read(), e.headers.get("Content-Encoding", "").lower())
            print(f"  HTTP Error {e.code}: {raw[:200]}")
            self._respond(e.code, raw)
        except Exception as e:
            import traceback; traceback.print_exc()
            self._respond(500, json.dumps({"error": str(e)}).encode())

    def _bulk_fix(self, body):
        try:
            payload     = json.loads(body)
            search_text = payload.get("search_text", "")
            page        = payload.get("page", 1)
            per_page    = min(payload.get("per_page", 100), 100)

            if not search_text:
                self._respond(400, json.dumps({"error": "search_text required"}).encode())
                return

            creds = base64.b64encode(f"{WC_CONSUMER_KEY}:{WC_CONSUMER_SECRET}".encode()).decode()

            # 1. Fetch products
            list_url = f"https://{WC_HOST}/wp-json/wc/v3/products?page={page}&per_page={per_page}&status=publish"
            list_req = urllib.request.Request(list_url, headers={
                "Authorization": f"Basic {creds}",
                "User-Agent": "Mozilla/5.0"
            })
            with urllib.request.urlopen(list_req, context=ssl_ctx) as r:
                raw = decompress(r.read(), r.headers.get("Content-Encoding","").lower())
            products = json.loads(raw)

            print(f"  Bulk fix: {len(products)} products, removing '{search_text}'")

            results = {"updated": 0, "skipped": 0, "failed": 0, "details": []}

            for p in products:
                pid   = p.get("id")
                name  = (p.get("name",""))[:50]
                desc  = p.get("description","")

                if search_text not in desc:
                    results["skipped"] += 1
                    results["details"].append({"id": pid, "name": name, "status": "skipped"})
                    continue

                # Remove the entire stat card div containing search_text
                # Match <div class="fs-stat-card...">...</div> containing the search text
                new_desc = re.sub(
                    r'<div class="fs-stat-card[^"]*">\s*<div class="fs-stat-icon">.*?</div>\s*<div class="fs-stat-content">.*?' + re.escape(search_text) + r'.*?</div>\s*</div>\s*</div>',
                    '',
                    desc,
                    flags=re.DOTALL
                )

                if new_desc == desc:
                    # Fallback: just remove the text and surrounding paragraph
                    new_desc = re.sub(
                        r'<[^>]+>' + re.escape(search_text) + r'</[^>]+>',
                        '',
                        desc
                    )

                try:
                    upd_data = json.dumps({"description": new_desc}, ensure_ascii=False).encode("utf-8")
                    upd_req  = urllib.request.Request(
                        f"https://{WC_HOST}/wp-json/wc/v3/products/{pid}",
                        data=upd_data,
                        headers={"Content-Type":"application/json","Authorization":f"Basic {creds}","User-Agent":"Mozilla/5.0"},
                        method="PUT"
                    )
                    with urllib.request.urlopen(upd_req, context=ssl_ctx) as r:
                        r.read()
                    print(f"  Updated #{pid} {name}")
                    results["updated"] += 1
                    results["details"].append({"id": pid, "name": name, "status": "updated"})
                except Exception as e:
                    print(f"  Failed #{pid}: {e}")
                    results["failed"] += 1
                    results["details"].append({"id": pid, "name": name, "status": f"failed: {str(e)[:50]}"})

            self._respond(200, json.dumps(results).encode())

        except Exception as e:
            import traceback; traceback.print_exc()
            self._respond(500, json.dumps({"error": str(e)}).encode())

    def _woo_id_lookup(self):
        from urllib.parse import urlparse, parse_qs
        qs   = parse_qs(urlparse(self.path).query)
        slug = qs.get("slug", [""])[0]
        if not slug:
            self._respond(400, json.dumps({"error": "slug required"}).encode())
            return
        try:
            creds = base64.b64encode(f"{WC_CONSUMER_KEY}:{WC_CONSUMER_SECRET}".encode()).decode()
            url   = f"https://{WC_HOST}/wp-json/wc/v3/products?slug={slug}&per_page=1"
            req   = urllib.request.Request(url, headers={
                "Authorization": f"Basic {creds}",
                "User-Agent": "Mozilla/5.0"
            })
            with urllib.request.urlopen(req, context=ssl_ctx) as r:
                data = json.loads(decompress(r.read(), r.headers.get("Content-Encoding","").lower()))
            if data:
                self._respond(200, json.dumps({"id": data[0]["id"], "name": data[0].get("name","")}).encode())
            else:
                self._respond(200, json.dumps({"id": None}).encode())
        except Exception as e:
            self._respond(200, json.dumps({"id": None, "error": str(e)}).encode())

    def _priority_lookup(self):
        qs  = parse_qs(urlparse(self.path).query)
        sku = qs.get("sku", [""])[0]
        if not sku:
            self._respond(400, json.dumps({"error": "SKU required"}).encode())
            return
        try:
            url   = f"https://agas.wee.co.il/odata/Priority/tabula.ini/agas/LOGPART?$filter=PARTNAME eq '{sku}'&$select=PARTNAME,PARTDES,STATDES&$top=1"
            creds = base64.b64encode(f"{PRIORITY_USER}:{PRIORITY_PASS}".encode()).decode()
            req   = urllib.request.Request(url, headers={"Authorization": f"Basic {creds}", "Accept": "application/json", "User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, context=ssl_ctx) as resp:
                raw = decompress(resp.read(), resp.headers.get("Content-Encoding", "").lower())
            self._respond(200, raw)
        except urllib.error.HTTPError as e:
            self._respond(e.code, decompress(e.read(), e.headers.get("Content-Encoding","").lower()))
        except Exception as e:
            self._respond(500, json.dumps({"error": str(e)}).encode())

    def _respond(self, code, data):
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(data if isinstance(data, bytes) else data.encode())

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    print(f"\n{'='*50}")
    print(f"  PromoPatriot Content Generator")
    print(f"{'='*50}")
    print(f"  WC Host : {WC_HOST or 'NOT SET'}")
    print(f"  Port    : {PORT}")
    print(f"{'='*50}\n")
    HTTPServer(("", PORT), Handler).serve_forever()