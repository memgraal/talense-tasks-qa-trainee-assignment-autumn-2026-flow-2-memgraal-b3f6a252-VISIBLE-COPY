"""API checks for the QA trainee assignment (standard-library only)."""

from __future__ import annotations

import json
import os
import re
import unittest
import uuid
from dataclasses import dataclass
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "https://qa-internship.avito.com"
UUID_PATTERN = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
    re.IGNORECASE,
)


@dataclass
class ApiResponse:
    status: int
    body: bytes
    headers: object

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            preview = self.body.decode("utf-8", errors="replace")[:300]
            raise AssertionError(f"Ответ не является JSON: {preview!r}") from exc


class ApiClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def request(self, method: str, path: str, *, payload=None, token=None, headers=None):
        request_headers = {"Accept": "application/json"}
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
        if token:
            request_headers["Authorization"] = f"Bearer {token}"
        if headers:
            request_headers.update(headers)

        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}",
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=25) as response:
                return ApiResponse(response.status, response.read(), response.headers)
        except HTTPError as response:
            return ApiResponse(response.code, response.read(), response.headers)
        except URLError as exc:
            raise RuntimeError(f"Не удалось обратиться к {self.base_url}: {exc.reason}") from exc


class InternshipApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = ApiClient(os.getenv("QA_API_BASE_URL", DEFAULT_BASE_URL))
        cls.username = f"qa_{uuid.uuid4().hex}"
        cls.password = f"Qa-{uuid.uuid4().hex}-9!"

        registration = cls.client.request(
            "POST",
            "/api/1/register",
            payload={"username": cls.username, "password": cls.password},
        )
        if registration.status != 201:
            raise AssertionError(
                "API-SETUP: регистрация пользователя для тестового набора должна вернуть "
                f"201, получено {registration.status}: {registration.body[:300]!r}"
            )
        user = registration.json()
        if not isinstance(user, dict) or not user.get("id") or user.get("username") != cls.username:
            raise AssertionError(f"API-SETUP: неожиданный ответ регистрации: {user!r}")
        cls.seller_id = user["id"]

        authorization = cls.client.request(
            "POST",
            "/api/1/autorize",
            payload={"username": cls.username, "password": cls.password},
        )
        if authorization.status != 200:
            raise AssertionError(
                "API-SETUP: авторизация тестового пользователя должна вернуть "
                f"200, получено {authorization.status}: {authorization.body[:300]!r}"
            )
        auth = authorization.json()
        cls.token = auth.get("accessToken") if isinstance(auth, dict) else None
        if not cls.token or auth.get("tokenType", "").lower() != "bearer":
            raise AssertionError("API-SETUP: ответ авторизации не содержит bearer accessToken")

    def setUp(self):
        self.created_item_ids = []

    def tearDown(self):
        for item_id in self.created_item_ids:
            try:
                self.client.request("DELETE", f"/api/2/item/{item_id}", token=self.token)
            except Exception:
                # Cleanup must not hide the assertion that caused the test to fail.
                pass

    def assert_status(self, response, expected, case_id, endpoint):
        expected_codes = (expected,) if isinstance(expected, int) else tuple(expected)
        preview = response.body.decode("utf-8", errors="replace")[:300]
        self.assertIn(
            response.status,
            expected_codes,
            f"{case_id}: {endpoint}: ожидался HTTP {expected_codes}, "
            f"получен {response.status}; ответ: {preview!r}",
        )

    def create_item(self, *, name=None, price=9900, statistics=None, seller_id=None, headers=None):
        item_name = name or f"qa-item-{uuid.uuid4().hex}"
        payload = {
            "sellerID": self.seller_id if seller_id is None else seller_id,
            "name": item_name,
            "price": price,
            "statistics": statistics or {"likes": 7, "viewCount": 11, "contacts": 3},
        }
        response = self.client.request(
            "POST", "/api/1/item", payload=payload, token=self.token, headers=headers
        )
        self.assert_status(response, 200, "API-07", "POST /api/1/item")
        body = response.json()
        status_text = body.get("status", "") if isinstance(body, dict) else ""
        match = UUID_PATTERN.search(str(status_text))
        self.assertIsNotNone(
            match,
            f"API-07: POST /api/1/item: ответ status не содержит UUID: {status_text!r}",
        )
        item_id = match.group(0)
        self.created_item_ids.append(item_id)
        return item_id, payload

    def test_api_01_registers_a_new_user(self):
        username = f"qa_{uuid.uuid4().hex}"
        password = f"Qa-{uuid.uuid4().hex}-9!"
        response = self.client.request(
            "POST", "/api/1/register", payload={"username": username, "password": password}
        )
        self.assert_status(response, 201, "API-01", "POST /api/1/register")
        body = response.json()
        self.assertEqual(body.get("username"), username, "API-01: username в ответе должен совпасть")
        self.assertTrue(body.get("id"), "API-01: ответ регистрации должен содержать id")

    def test_api_02_rejects_duplicate_username(self):
        response = self.client.request(
            "POST",
            "/api/1/register",
            payload={"username": self.username, "password": self.password},
        )
        self.assert_status(response, 409, "API-02", "POST /api/1/register с существующим username")

    def test_api_03_rejects_registration_with_missing_or_blank_credentials(self):
        cases = (
            ("без username", {"password": f"Qa-{uuid.uuid4().hex}-9!"}),
            ("пустые поля", {"username": "", "password": ""}),
        )
        for label, payload in cases:
            with self.subTest(case=label):
                response = self.client.request("POST", "/api/1/register", payload=payload)
                self.assert_status(response, (400, 422), "API-03", f"регистрация: {label}")

    def test_api_04_authorizes_valid_credentials(self):
        response = self.client.request(
            "POST",
            "/api/1/autorize",
            payload={"username": self.username, "password": self.password},
        )
        self.assert_status(response, 200, "API-04", "POST /api/1/autorize")
        body = response.json()
        self.assertTrue(body.get("accessToken"), "API-04: accessToken не должен быть пустым")
        self.assertEqual(body.get("tokenType", "").lower(), "bearer", "API-04: ожидается tokenType Bearer")

    def test_api_05_rejects_wrong_password(self):
        response = self.client.request(
            "POST",
            "/api/1/autorize",
            payload={"username": self.username, "password": f"wrong-{uuid.uuid4().hex}"},
        )
        self.assert_status(response, 401, "API-05", "POST /api/1/autorize с неверным паролем")

    def test_api_06_all_protected_routes_require_a_bearer_token(self):
        item_id, _ = self.create_item()
        missing = str(uuid.uuid4())
        requests = (
            ("POST", "/api/1/item", {"sellerID": self.seller_id, "name": "unauthorized", "price": 1, "statistics": {}}),
            ("GET", f"/api/1/item/{item_id}", None),
            ("GET", f"/api/1/{self.seller_id}/item", None),
            ("GET", f"/api/1/statistic/{item_id}", None),
            ("GET", f"/api/2/statistic/{item_id}", None),
            ("DELETE", f"/api/2/item/{item_id}", None),
            ("GET", f"/api/1/item/{missing}", None),
        )
        for method, path, payload in requests:
            with self.subTest(method=method, path=path):
                response = self.client.request(method, path, payload=payload)
                if method == "POST" and response.status == 200:
                    body = response.json()
                    match = UUID_PATTERN.search(str(body.get("status", ""))) if isinstance(body, dict) else None
                    if match:
                        self.created_item_ids.append(match.group(0))
                self.assert_status(response, (401, 403), "API-06", f"защищённый маршрут {method} {path}")

    def test_api_07_rejects_seller_id_that_does_not_match_token(self):
        other_seller_id = int(self.seller_id) + 1000000
        response = self.client.request(
            "POST",
            "/api/1/item",
            token=self.token,
            payload={
                "sellerID": other_seller_id,
                "name": f"qa-owner-mismatch-{uuid.uuid4().hex}",
                "price": 100,
                "statistics": {"likes": 0, "viewCount": 0, "contacts": 0},
            },
        )
        if response.status == 200:
            body = response.json()
            match = UUID_PATTERN.search(str(body.get("status", ""))) if isinstance(body, dict) else None
            if match:
                self.created_item_ids.append(match.group(0))
        self.assert_status(response, (400, 403, 422), "API-07", "POST /api/1/item с чужим sellerID")

    def test_api_08_x_userid_cannot_impersonate_another_seller(self):
        other_seller_id = int(self.seller_id) + 1000000
        response = self.client.request(
            "POST",
            "/api/1/item",
            token=self.token,
            headers={"X-UserId": str(other_seller_id)},
            payload={
                "sellerID": other_seller_id,
                "name": f"qa-spoof-{uuid.uuid4().hex}",
                "price": 100,
                "statistics": {"likes": 0, "viewCount": 0, "contacts": 0},
            },
        )
        if response.status == 200:
            body = response.json()
            match = UUID_PATTERN.search(str(body.get("status", ""))) if isinstance(body, dict) else None
            if match:
                self.created_item_ids.append(match.group(0))
        self.assert_status(response, (400, 403, 422), "API-08", "POST /api/1/item с поддельным X-UserId")

    def test_api_09_create_get_and_list_return_the_new_item(self):
        item_id, payload = self.create_item()

        detail_response = self.client.request("GET", f"/api/1/item/{item_id}", token=self.token)
        self.assert_status(detail_response, 200, "API-09", "GET /api/1/item/{id}")
        detail = detail_response.json()
        self.assertIsInstance(detail, list, "API-09: ручка объявления должна вернуть массив")
        self.assertTrue(any(item.get("id") == item_id for item in detail), "API-09: созданное объявление не найдено")
        item = next(item for item in detail if item.get("id") == item_id)
        self.assertEqual(item.get("name"), payload["name"], "API-09: name изменился")
        self.assertEqual(item.get("price"), payload["price"], "API-09: price изменился")
        self.assertEqual(int(item.get("sellerId")), int(self.seller_id), "API-09: неверный sellerId")
        self.assertEqual(item.get("statistics"), payload["statistics"], "API-09: statistics изменились")

        list_response = self.client.request("GET", f"/api/1/{self.seller_id}/item", token=self.token)
        self.assert_status(list_response, 200, "API-09", "GET /api/1/{sellerID}/item")
        seller_items = list_response.json()
        self.assertIsInstance(seller_items, list, "API-09: список объявлений продавца должен быть массивом")
        self.assertTrue(
            any(item.get("id") == item_id for item in seller_items),
            "API-09: объявление отсутствует в списке своего продавца",
        )

    def test_api_10_duplicate_business_fields_still_get_unique_uuids(self):
        name = f"qa-duplicate-{uuid.uuid4().hex}"
        statistics = {"likes": 2, "viewCount": 5, "contacts": 1}
        first, _ = self.create_item(name=name, price=321, statistics=statistics)
        second, _ = self.create_item(name=name, price=321, statistics=statistics)
        self.assertNotEqual(first, second, "API-10: объявления с одинаковыми данными должны иметь разные UUID")

    def test_api_11_and_12_both_statistics_versions_return_saved_values(self):
        statistics = {"likes": 17, "viewCount": 23, "contacts": 4}
        item_id, _ = self.create_item(statistics=statistics)
        for version, case_id in ((1, "API-11"), (2, "API-12")):
            path = f"/api/{version}/statistic/{item_id}"
            with self.subTest(version=version):
                response = self.client.request("GET", path, token=self.token)
                self.assert_status(response, 200, case_id, path)
                body = response.json()
                self.assertIsInstance(body, list, f"{case_id}: ожидался массив статистики")
                self.assertTrue(body, f"{case_id}: массив статистики пуст")
                self.assertEqual(body[0], statistics, f"{case_id}: статистика не совпадает с созданной")

    def test_api_13_delete_removes_the_created_item(self):
        item_id, _ = self.create_item()
        response = self.client.request("DELETE", f"/api/2/item/{item_id}", token=self.token)
        self.assert_status(response, 200, "API-13", "DELETE /api/2/item/{id}")
        self.created_item_ids.remove(item_id)

        after_delete = self.client.request("GET", f"/api/1/item/{item_id}", token=self.token)
        self.assert_status(after_delete, (200, 404), "API-13", "GET после удаления")
        if after_delete.status == 200:
            body = after_delete.json()
            self.assertTrue(
                isinstance(body, list) and not any(item.get("id") == item_id for item in body),
                "API-13: удалённое объявление всё ещё доступно по UUID",
            )

    def test_api_14_malformed_item_uuid_is_rejected_without_server_error(self):
        response = self.client.request("GET", "/api/1/item/not-a-uuid", token=self.token)
        self.assert_status(response, (400, 404), "API-14", "GET /api/1/item/not-a-uuid")

    def test_api_15_unknown_uuid_does_not_return_another_item(self):
        unknown_id = str(uuid.uuid4())
        response = self.client.request("GET", f"/api/1/item/{unknown_id}", token=self.token)
        self.assert_status(response, (200, 404), "API-15", "GET /api/1/item/{несуществующий UUID}")
        if response.status == 200:
            self.assertEqual(response.json(), [], "API-15: неизвестный UUID должен дать пустой массив")


if __name__ == "__main__":
    unittest.main(verbosity=2)
