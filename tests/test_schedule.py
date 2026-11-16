import asyncio
import json
import tempfile
import time
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.sources.schedule import (
    _class_applies_to_week,
    _is_relevant_schedule_document,
    _read_cached_documents,
    get_group_schedule,
    select_group_schedule,
)


class ScheduleTests(unittest.IsolatedAsyncioTestCase):
    def test_fresh_cache_reuses_requested_documents_with_ignored_extras(self):
        requested = {"title": "2 курс", "url": "https://example.test/course.xlsx"}
        ignored = {"title": "распоряжение", "url": "https://example.test/order.pdf"}
        payloads = [{**requested, "text": "schedule", "sheets": []},
                    {**ignored, "text": "order", "sheets": []}]
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "schedule.json"
            cache_path.write_text(json.dumps({
                "version": 2, "saved_at": time.time(),
                "urls": [requested["url"], ignored["url"]], "documents": payloads,
            }), encoding="utf-8")

            result = _read_cached_documents([requested], cache_path, time.time())

        self.assertEqual(result, [payloads[0]])

    def test_schedule_discovery_excludes_administrative_session_order(self):
        self.assertTrue(_is_relevant_schedule_document({
            "title": "2 курс",
            "url": "https://kpfu.ru/portal/file?name=Raspisanie.2.kurs.1.sem.2026_27.xlsx",
        }))
        self.assertFalse(_is_relevant_schedule_document({
            "title": "Распоряжение о сроках дополнительных сессий в 2026/27 учебном году",
            "url": "https://kpfu.ru/portal/file?name=image_t0000000159_n2.pdf",
        }))

    def test_even_and_odd_week_labels_are_not_confused(self):
        self.assertTrue(_class_applies_to_week("Лекция (нечетная неделя)", 1))
        self.assertFalse(_class_applies_to_week("Лекция (нечетная неделя)", 2))
        self.assertFalse(_class_applies_to_week("Лекция (четная неделя)", 1))
        self.assertTrue(_class_applies_to_week("Лекция (четная неделя)", 2))

    async def test_empty_group_is_rejected_without_fetching_schedule_page(self):
        with patch("app.sources.schedule._published_documents", new=AsyncMock()) as published_documents:
            result = await get_group_schedule("   ")

        published_documents.assert_not_awaited()
        self.assertEqual(result["status"], "invalid_group")

    def test_group_selection_matches_exact_header_and_only_its_columns(self):
        sheet = {"name": "1 курс", "rows": [
            ["", "Номер группы", "01.2-621", "", "01.2-622"],
            ["", "Номер п/группы", "01.2-621а", "01.2-621б", "01.2-622а"],
            ["понедельник", "8.30-10.00", "Анатомия", "Биология", "История"],
        ]}

        result = select_group_schedule([sheet], "01.2-621")
        absent = select_group_schedule([sheet], "01.2-6210")

        self.assertEqual(result, [
            {"sheet": "1 курс", "weekday": "понедельник", "time": "8.30-10.00",
             "source_time": "8.30-10.00", "subgroup": "01.2-621а", "classes": ["Анатомия"]},
            {"sheet": "1 курс", "weekday": "понедельник", "time": "8.30-10.00",
             "source_time": "8.30-10.00", "subgroup": "01.2-621б", "classes": ["Биология"]},
        ])
        self.assertIsNone(absent)

    def test_parent_group_keeps_subgroups_separate_and_prefers_cell_time(self):
        sheet = {"name": "2 курс", "rows": [
            ["", "Номер группы", "01.2-513", ""],
            ["", "Номер п/группы", "01.2-513а", "01.2-513б"],
            ["понедельник", "10.10-11.40", '10.00-11.30 Элективные курсы (СК "Бустан")',
             '10.00-11.30 Элективные курсы (СК "Бустан")'],
            ["", "12.10-13.40", "Статистические методы", ""],
        ]}

        result = select_group_schedule([sheet], "01.2-513")

        self.assertEqual(
            result,
            [
                {"sheet": "2 курс", "weekday": "понедельник", "time": "10.00-11.30",
                 "source_time": "10.10-11.40", "subgroup": "01.2-513а",
                 "classes": ['Элективные курсы (СК "Бустан")']},
                {"sheet": "2 курс", "weekday": "понедельник", "time": "10.00-11.30",
                 "source_time": "10.10-11.40", "subgroup": "01.2-513б",
                 "classes": ['Элективные курсы (СК "Бустан")']},
                {"sheet": "2 курс", "weekday": "понедельник", "time": "12.10-13.40",
                 "source_time": "12.10-13.40", "subgroup": "01.2-513а",
                 "classes": ["Статистические методы"]},
            ],
        )

    def test_target_date_filters_weekday_and_active_academic_week(self):
        sheet = {"name": "1 курс", "rows": [
            ["", "Номер группы", "01.2-621", "", "01.2-622"],
            ["", "Номер п/группы", "01.2-621а", "01.2-621б", "01.2-622а"],
            ["понедельник", "8.30-10.00", "Введение (1 нед.)", "Анатомия (2-18 нед.)", "История"],
            ["вторник", "10.10-11.40", "Химия", "Биология", "История"],
        ]}

        result = select_group_schedule(
            [sheet], "01.2-621", date(2026, 9, 7), term_start=date(2026, 9, 1)
        )

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["weekday"], "понедельник")
        self.assertEqual(result[0]["classes"], ["Анатомия (2-18 нед.)"])

    async def test_document_downloads_use_bounded_concurrency(self):
        docs = [{"title": f"doc-{i}", "url": f"https://example.test/2026_27/{i}.pdf"} for i in range(12)]
        current = 0
        max_current = 0

        async def fake_get_bytes(_url):
            nonlocal current, max_current
            current += 1
            max_current = max(max_current, current)
            await asyncio.sleep(0.01)
            current -= 1
            return b"pdf"

        with (
            tempfile.TemporaryDirectory() as temp_dir,
            patch("app.sources.schedule._published_documents", new=AsyncMock(return_value=docs)),
            patch("app.sources.schedule.get_bytes", side_effect=fake_get_bytes),
            patch("app.sources.schedule._extract_document", return_value="no matching group"),
        ):
            result = await get_group_schedule("ИВТ-21", cache_path=Path(temp_dir) / "schedule.json")

        self.assertEqual(result["status"], "incomplete_source_coverage")
        self.assertGreater(max_current, 1)
        self.assertLessEqual(max_current, 6)

    async def test_fresh_cache_avoids_redownloading_documents(self):
        docs = [{"title": "doc", "url": "https://example.test/2026_27/1.xlsx"}]
        sheet = {"name": "1 курс", "rows": [
            ["", "Номер группы", "01.2-621"],
            ["", "Номер п/группы", "01.2-621а"],
            ["понедельник", "8.30-10.00", "Анатомия"],
        ]}
        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "schedule.json"
            with (
                patch("app.sources.schedule._published_documents", new=AsyncMock(return_value=docs)),
                patch("app.sources.schedule.get_bytes", new=AsyncMock(return_value=b"pdf")) as get_bytes,
                patch("app.sources.schedule._extract_xlsx_payload", return_value={"text": "", "sheets": [sheet]}),
            ):
                first = await get_group_schedule("01.2-621", cache_path=cache_path)
                second = await get_group_schedule("01.2-621", cache_path=cache_path)

        self.assertEqual(first["status"], "ok")
        self.assertEqual(second["status"], "ok")
        get_bytes.assert_awaited_once()

    async def test_lookup_filters_old_year_and_returns_only_group_and_date(self):
        docs = [
            {"title": "1 курс", "url": "https://example.test/1.sem..2026_27.xlsx"},
            {"title": "old 1 курс", "url": "https://example.test/1.sem..2025_26.xlsx"},
        ]
        current_sheet = {"name": "1 курс", "rows": [
            ["", "Номер группы", "01.2-621", "", "01.2-622"],
            ["", "Номер п/группы", "01.2-621а", "01.2-621б", "01.2-622а"],
            ["Дата", "2026-09-01"],
            ["понедельник", "8.30-10.00", "Введение (1 нед.)", "Анатомия (2-18 нед.)", "История"],
            ["вторник", "10.10-11.40", "Химия", "Биология", "История"],
        ]}

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch("app.sources.schedule._published_documents", new=AsyncMock(return_value=docs)),
                patch("app.sources.schedule.get_bytes", new=AsyncMock(return_value=b"xlsx")) as get_bytes,
                patch("app.sources.schedule._extract_xlsx_payload", return_value={"text": "", "sheets": [current_sheet]}),
            ):
                result = await get_group_schedule(
                    "01.2-621", date(2026, 9, 7), cache_path=Path(temp_dir) / "schedule.json"
                )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["sources"][0]["classes"][0]["weekday"], "понедельник")
        self.assertEqual(result["sources"][0]["classes"][0]["classes"], ["Анатомия (2-18 нед.)"])
        get_bytes.assert_awaited_once_with(docs[0]["url"])

    async def test_unparsed_public_file_prevents_claiming_complete_schedule(self):
        docs = [
            {"title": "1 курс", "url": "https://example.test/1.sem..2026_27.xlsx"},
            {"title": "Дополнительное расписание", "url": "https://example.test/1.sem..2026_27.pdf"},
        ]
        sheet = {"name": "1 курс", "rows": [
            ["", "Номер группы", "01.2-621"],
            ["", "Номер п/группы", "01.2-621а"],
            ["понедельник", "8.30-10.00", "Анатомия"],
        ]}

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch("app.sources.schedule._published_documents", new=AsyncMock(return_value=docs)),
                patch("app.sources.schedule.get_bytes", new=AsyncMock(return_value=b"data")),
                patch("app.sources.schedule._extract_xlsx_payload", return_value={"text": "", "sheets": [sheet]}),
                patch("app.sources.schedule._extract_document", return_value="PDF text mentioning another group"),
            ):
                result = await get_group_schedule("01.2-621", cache_path=Path(temp_dir) / "schedule.json")

        self.assertEqual(result["status"], "incomplete_source_coverage")
        self.assertEqual(result["unverified_files"], 1)
        self.assertEqual(result["sources"][0]["classes"][0]["classes"], ["Анатомия"])

    async def test_failed_download_prevents_complete_result_even_if_group_was_found(self):
        docs = [
            {"title": "1 курс", "url": "https://example.test/1.sem..2026_27.xlsx"},
            {"title": "Дополнительная таблица", "url": "https://example.test/1.sem..2026_27.part.xlsx"},
        ]
        sheet = {"name": "1 курс", "rows": [
            ["", "Номер группы", "01.2-621"],
            ["", "Номер п/группы", "01.2-621а"],
            ["понедельник", "8.30-10.00", "Анатомия"],
        ]}

        async def download(url):
            if url.endswith("1.sem..2026_27.part.xlsx"):
                raise OSError("network down")
            return b"xlsx"

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch("app.sources.schedule._published_documents", new=AsyncMock(return_value=docs)),
                patch("app.sources.schedule.get_bytes", side_effect=download),
                patch("app.sources.schedule._extract_xlsx_payload", return_value={"text": "", "sheets": [sheet]}),
            ):
                result = await get_group_schedule("01.2-621", cache_path=Path(temp_dir) / "schedule.json")

        self.assertEqual(result["status"], "source_error")
        self.assertEqual(result["unverified_files"], 1)
        self.assertEqual(result["sources"][0]["classes"][0]["classes"], ["Анатомия"])

    async def test_date_in_another_group_file_is_not_used_as_semester_start(self):
        docs = [
            {"title": "Группа 621", "url": "https://example.test/1.sem..2026_27.621.xlsx"},
            {"title": "Группа 622", "url": "https://example.test/1.sem..2026_27.622.xlsx"},
        ]
        payloads = {
            docs[0]["url"]: {"text": "", "sheets": [{"name": "621", "rows": [
                ["", "Номер группы", "01.2-621"],
                ["", "Номер п/группы", "01.2-621а"],
                ["понедельник", "8.30-10.00", "Анатомия"],
            ]}]},
            docs[1]["url"]: {"text": "", "sheets": [{"name": "622", "rows": [
                ["Дата", "2026-09-01"],
                ["", "Номер группы", "01.2-622"],
            ]}]},
        }

        async def download(url):
            return url.encode()

        with tempfile.TemporaryDirectory() as temp_dir:
            with (
                patch("app.sources.schedule._published_documents", new=AsyncMock(return_value=docs)),
                patch("app.sources.schedule.get_bytes", side_effect=download),
                patch("app.sources.schedule._extract_xlsx_payload", side_effect=lambda data: payloads[data.decode()]),
            ):
                result = await get_group_schedule(
                    "01.2-621", date(2026, 9, 7), cache_path=Path(temp_dir) / "schedule.json"
                )

        self.assertEqual(result["status"], "date_filter_unavailable")


if __name__ == "__main__":
    unittest.main()
