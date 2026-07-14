#!/bin/bash

# Shared bounded readiness polling for deploy/deploy_dev.sh and the
# wake-dev-ecs composite action (issue #1140).
#
# The deadline is based on Python's monotonic clock, so wall-clock changes do
# not lengthen or shorten a deploy. curl time and sleep time consume the SAME
# deadline. Callers may provide a verifier function; after the required number
# of consecutive HTTP matches, that verifier must also pass before this helper
# returns success.

# These globals are the helper's caller-facing diagnostics API.
# shellcheck disable=SC2034

readiness_is_non_negative_integer() {
    case "$1" in
        ''|*[!0-9]*)
            return 1
            ;;
        *)
            return 0
            ;;
    esac
}

readiness_is_positive_integer() {
    readiness_is_non_negative_integer "$1" && [ "$1" -gt 0 ]
}

readiness_monotonic_seconds() {
    "${READINESS_PYTHON_BIN:-python3}" -c \
        'import time; print(int(time.monotonic()))'
}

readiness_response_state() {
    local RESPONSE=$1
    local EXPECTED_TEXT=$2

    if [ -z "${RESPONSE}" ]; then
        echo "empty-or-unreachable"
    elif [ -n "${EXPECTED_TEXT}" ] && [ "${RESPONSE}" = "${EXPECTED_TEXT}" ]; then
        echo "exact-match"
    elif [ -n "${EXPECTED_TEXT}" ]; then
        echo "non-matching"
    else
        echo "non-empty"
    fi
}

readiness_validate_configuration() {
    local TIMEOUT_SECONDS=$1
    local POLL_SECONDS=$2
    local MAX_ATTEMPTS=$3
    local REQUIRED_MATCHES=$4

    if ! readiness_is_positive_integer "${TIMEOUT_SECONDS}"; then
        echo "ERROR: readiness timeout must be a positive integer; got '${TIMEOUT_SECONDS}'." >&2
        return 1
    fi
    if ! readiness_is_non_negative_integer "${POLL_SECONDS}"; then
        echo "ERROR: readiness poll interval must be a non-negative integer; got '${POLL_SECONDS}'." >&2
        return 1
    fi
    if ! readiness_is_positive_integer "${MAX_ATTEMPTS}"; then
        echo "ERROR: readiness max attempts must be a positive integer; got '${MAX_ATTEMPTS}'." >&2
        return 1
    fi
    if ! readiness_is_positive_integer "${REQUIRED_MATCHES}"; then
        echo "ERROR: readiness required matches must be a positive integer; got '${REQUIRED_MATCHES}'." >&2
        return 1
    fi
}

readiness_response_matches() {
    local RESPONSE=$1
    local EXPECTED_TEXT=$2

    if [ -n "${EXPECTED_TEXT}" ]; then
        [ "${RESPONSE}" = "${EXPECTED_TEXT}" ]
    else
        [ -n "${RESPONSE}" ]
    fi
}

readiness_poll_until_stable() {
    local URL=$1
    local EXPECTED_TEXT=$2
    local TIMEOUT_SECONDS=$3
    local POLL_SECONDS=$4
    local MAX_ATTEMPTS=$5
    local REQUIRED_MATCHES=$6
    local VERIFIER=${7:-}

    readiness_validate_configuration \
        "${TIMEOUT_SECONDS}" "${POLL_SECONDS}" \
        "${MAX_ATTEMPTS}" "${REQUIRED_MATCHES}" || return 1

    local STARTED_AT
    local DEADLINE
    local NOW
    local REMAINING
    local CURL_TIMEOUT
    local SLEEP_SECONDS
    local RESPONSE=""
    local RESPONSE_STATE="empty-or-unreachable"
    local RESPONSE_BYTES=0
    local ATTEMPT=0
    local CONSECUTIVE=0

    STARTED_AT=$(readiness_monotonic_seconds) || return 1
    DEADLINE=$((STARTED_AT + TIMEOUT_SECONDS))

    READINESS_STARTED_AT=${STARTED_AT}
    READINESS_DEADLINE=${DEADLINE}
    READINESS_ATTEMPTS=0
    READINESS_CONSECUTIVE=0
    READINESS_ELAPSED_SECONDS=0
    READINESS_LAST_RESPONSE_STATE="empty-or-unreachable"
    READINESS_LAST_RESPONSE_BYTES=0

    echo "Polling ${URL} for a stable response for at most ${TIMEOUT_SECONDS}s "\
         "(${REQUIRED_MATCHES} consecutive match(es), max ${MAX_ATTEMPTS} attempts)..."

    while [ "${ATTEMPT}" -lt "${MAX_ATTEMPTS}" ]; do
        NOW=$(readiness_monotonic_seconds) || return 1
        REMAINING=$((DEADLINE - NOW))
        if [ "${REMAINING}" -le 0 ]; then
            break
        fi

        ATTEMPT=$((ATTEMPT + 1))
        CURL_TIMEOUT=10
        if [ "${REMAINING}" -lt "${CURL_TIMEOUT}" ]; then
            CURL_TIMEOUT=${REMAINING}
        fi

        RESPONSE=$(curl -fsSL --max-time "${CURL_TIMEOUT}" "${URL}" 2>/dev/null || true)
        NOW=$(readiness_monotonic_seconds) || return 1
        READINESS_ELAPSED_SECONDS=$((NOW - STARTED_AT))
        READINESS_ATTEMPTS=${ATTEMPT}
        RESPONSE_STATE=$(readiness_response_state "${RESPONSE}" "${EXPECTED_TEXT}")
        RESPONSE_BYTES=$(printf '%s' "${RESPONSE}" | wc -c | tr -d '[:space:]')
        READINESS_LAST_RESPONSE_STATE=${RESPONSE_STATE}
        READINESS_LAST_RESPONSE_BYTES=${RESPONSE_BYTES}

        # A response that completed at/after the deadline is evidence for
        # diagnostics only; it must never turn the deploy green.
        if [ "${NOW}" -ge "${DEADLINE}" ]; then
            echo "Readiness attempt ${ATTEMPT}/${MAX_ATTEMPTS} completed at the hard deadline "\
                 "(elapsed=${READINESS_ELAPSED_SECONDS}s); rejecting the response."
            break
        fi

        if readiness_response_matches "${RESPONSE}" "${EXPECTED_TEXT}"; then
            CONSECUTIVE=$((CONSECUTIVE + 1))
        else
            CONSECUTIVE=0
        fi
        READINESS_CONSECUTIVE=${CONSECUTIVE}

        # Never print the response body: /ping is expected to be harmless,
        # but this shared action accepts arbitrary endpoints and their bodies
        # may contain credentials or other sensitive diagnostics.
        echo "Readiness attempt ${ATTEMPT}/${MAX_ATTEMPTS}: "\
             "response_state=${RESPONSE_STATE} response_bytes=${RESPONSE_BYTES} "\
             "expectation=$([ -n "${EXPECTED_TEXT}" ] && echo exact || echo non-empty) "\
             "consecutive=${CONSECUTIVE}/${REQUIRED_MATCHES} "\
             "elapsed=${READINESS_ELAPSED_SECONDS}s."

        if [ "${CONSECUTIVE}" -ge "${REQUIRED_MATCHES}" ]; then
            if [ -z "${VERIFIER}" ] || "${VERIFIER}" "${DEADLINE}"; then
                NOW=$(readiness_monotonic_seconds) || return 1
                READINESS_ELAPSED_SECONDS=$((NOW - STARTED_AT))
                if [ "${NOW}" -lt "${DEADLINE}" ]; then
                    return 0
                fi
                echo "Readiness verification completed after the hard deadline; rejecting success."
                break
            fi
            echo "Stable HTTP response reached, but the deployment-state "\
                 "verifier is not ready; continuing within the same deadline."
            CONSECUTIVE=0
            READINESS_CONSECUTIVE=0
        fi

        # Never sleep after the final attempt, and never let sleep extend the
        # monotonic deadline. curl time has already consumed the same budget.
        if [ "${ATTEMPT}" -ge "${MAX_ATTEMPTS}" ]; then
            break
        fi
        NOW=$(readiness_monotonic_seconds) || return 1
        REMAINING=$((DEADLINE - NOW))
        if [ "${REMAINING}" -le 0 ]; then
            break
        fi
        SLEEP_SECONDS=${POLL_SECONDS}
        if [ "${REMAINING}" -lt "${SLEEP_SECONDS}" ]; then
            SLEEP_SECONDS=${REMAINING}
        fi
        if [ "${SLEEP_SECONDS}" -gt 0 ]; then
            sleep "${SLEEP_SECONDS}"
        fi
    done

    NOW=$(readiness_monotonic_seconds) || return 1
    READINESS_ELAPSED_SECONDS=$((NOW - STARTED_AT))
    READINESS_ATTEMPTS=${ATTEMPT}
    READINESS_CONSECUTIVE=${CONSECUTIVE}
    READINESS_LAST_RESPONSE_STATE=${RESPONSE_STATE}
    READINESS_LAST_RESPONSE_BYTES=${RESPONSE_BYTES}
    return 1
}
