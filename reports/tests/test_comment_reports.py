import inspect

import pytest

from reports import services as reports_services
from reports.models import Report
from reports.tests.helpers import (
    client_for,
    make_comment,
    make_target,
    make_user,
    submit,
)
from social import services as social_services


@pytest.fixture
def comment(business):
    post = make_target("post", business)
    return make_comment(post, make_user("author"))


@pytest.mark.django_db
class TestCommentReportPipeline:
    def test_single_report_increments_reports_count(self, comment):
        response = submit(client_for(make_user("r1")), "comment", comment.pk)
        assert response.status_code == 201
        comment.refresh_from_db()
        assert comment.reports_count == 1
        assert comment.is_hidden is False
        assert Report.objects.count() == 1

    def test_below_threshold_does_not_hide(self, comment):
        threshold = social_services.COMMENT_AUTO_HIDE_THRESHOLD
        for i in range(threshold - 1):
            submit(client_for(make_user(f"r{i}")), "comment", comment.pk)
        comment.refresh_from_db()
        assert comment.reports_count == threshold - 1
        assert comment.is_hidden is False

    def test_threshold_reports_from_different_users_hide_the_comment(self, comment):
        """The real end-to-end proof of P-055's mechanism via genuine API calls."""
        threshold = social_services.COMMENT_AUTO_HIDE_THRESHOLD
        for i in range(threshold):
            response = submit(client_for(make_user(f"r{i}")), "comment", comment.pk)
            assert response.status_code == 201
            comment.refresh_from_db()
            assert comment.reports_count == i + 1
            assert comment.is_hidden is (i + 1 >= threshold)
        assert Report.objects.count() == threshold

    def test_reporting_an_already_hidden_comment_is_404(self, comment):
        threshold = social_services.COMMENT_AUTO_HIDE_THRESHOLD
        for i in range(threshold):
            submit(client_for(make_user(f"r{i}")), "comment", comment.pk)
        comment.refresh_from_db()
        assert comment.is_hidden is True

        response = submit(client_for(make_user("late")), "comment", comment.pk)
        assert response.status_code == 404
        assert Report.objects.count() == threshold

    def test_one_user_repeating_cannot_hide_a_comment(self, comment):
        client = client_for(make_user("spammer"))
        statuses = [submit(client, "comment", comment.pk).status_code for _ in range(4)]
        assert statuses == [201, 200, 200, 200]
        comment.refresh_from_db()
        assert comment.reports_count == 1
        assert comment.is_hidden is False

    def test_only_the_reported_comment_is_affected(self, business, comment):
        other = make_comment(make_target("post", business), make_user("other"))
        submit(client_for(make_user("r1")), "comment", comment.pk)
        other.refresh_from_db()
        assert other.reports_count == 0
        assert other.is_hidden is False


@pytest.mark.django_db
class TestPipelineCallsP055Service:
    @pytest.fixture
    def spy(self, monkeypatch):
        calls = []
        real = social_services.check_and_hide_if_threshold_exceeded

        def _spy(comment):
            calls.append(comment.pk)
            return real(comment)

        monkeypatch.setattr(
            social_services, "check_and_hide_if_threshold_exceeded", _spy
        )
        return calls

    def test_service_called_once_for_a_new_comment_report(self, comment, spy):
        submit(client_for(make_user("r1")), "comment", comment.pk)
        assert spy == [comment.pk]

    def test_service_not_called_for_a_duplicate_report(self, comment, spy):
        client = client_for(make_user("r1"))
        submit(client, "comment", comment.pk)
        submit(client, "comment", comment.pk)
        assert spy == [comment.pk]

    def test_service_not_called_for_non_comment_targets(self, business, spy):
        post = make_target("post", business)
        submit(client_for(make_user("r1")), "post", post.pk)
        assert spy == []

    def test_failure_rolls_back_report_and_counter(self, comment, monkeypatch):
        def boom(_comment):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            social_services, "check_and_hide_if_threshold_exceeded", boom
        )
        client = client_for(make_user("r1"))
        client.raise_request_exception = False

        response = submit(client, "comment", comment.pk)

        assert response.status_code == 500
        assert Report.objects.count() == 0
        comment.refresh_from_db()
        assert comment.reports_count == 0


class TestPipelineStructure:
    def test_submit_report_is_atomic_f_based_and_does_not_reimplement_threshold(
        self,
    ):
        source = inspect.getsource(reports_services)
        assert "transaction.atomic" in source
        assert 'F("reports_count")' in source
        assert "check_and_hide_if_threshold_exceeded" in source
        # The threshold constant belongs to P-055 only.
        assert "COMMENT_AUTO_HIDE_THRESHOLD" not in source
