import io
import logging

from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase
from django.utils.log import log_response

from website.gunicorn_logging import redact_token_path
from website.logging_config import RequestContextFilter


class ProductionLoggingTests(SimpleTestCase):
    def test_production_django_request_logs_traceback_and_request_id(self):
        output = io.StringIO()
        handler = logging.StreamHandler(output)
        handler.addFilter(RequestContextFilter())
        handler.setFormatter(logging.Formatter(
            'request_id=%(request_id)s correlation_id=%(correlation_id)s %(message)s'
        ))
        logger = logging.getLogger('django.request')
        original_level = logger.level
        original_propagate = logger.propagate
        original_handlers = logger.handlers[:]
        logger.handlers[:] = [handler]
        logger.setLevel(logging.ERROR)
        logger.propagate = False
        request = RequestFactory().post(
            '/accounts/password/reset/',
            data={'email': 'private@example.test', 'password': 'never-log-this'},
        )
        request.request_id = 'test-request-id'
        request.correlation_id = 'test-correlation-id'
        try:
            try:
                raise RuntimeError('controlled request failure')
            except RuntimeError:
                log_response(
                    'Internal Server Error: %s',
                    request.path,
                    response=HttpResponse(status=500),
                    request=request,
                    exception=True,
                )
        finally:
            logger.handlers[:] = original_handlers
            logger.setLevel(original_level)
            logger.propagate = original_propagate

        rendered = output.getvalue()
        self.assertIn('request_id=test-request-id', rendered)
        self.assertIn('correlation_id=test-correlation-id', rendered)
        self.assertIn('RuntimeError: controlled request failure', rendered)
        self.assertIn('Traceback (most recent call last)', rendered)
        self.assertNotIn('private@example.test', rendered)
        self.assertNotIn('never-log-this', rendered)

    def test_logging_configuration_uses_stdout_for_django_errors(self):
        from django.conf import settings

        console = settings.LOGGING['handlers']['console']
        self.assertEqual(console['class'], 'logging.StreamHandler')
        self.assertEqual(console['stream'], 'ext://sys.stdout')
        self.assertIn(
            'console',
            settings.LOGGING['loggers']['django.request']['handlers'],
        )
        self.assertEqual(settings.LOGGING['loggers']['django.request']['level'], 'ERROR')

    def test_recipient_token_access_log_redaction_remains_enabled(self):
        self.assertEqual(
            redact_token_path('/t/c/recipient-secret'),
            '/t/c/[token]',
        )
