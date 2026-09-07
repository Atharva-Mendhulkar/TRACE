#!/usr/bin/env bash
# ==============================================================================
# TRACE End-to-End CLI Verification Test Script with Mock Data
# Executes all 11 subcommands to completely test and showcase the semantic CLI.
# ==============================================================================
set -e

# Resolve repository root
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

# Activate virtual environment if present
if [ -d "${REPO_ROOT}/.venv" ]; then
    export PATH="${REPO_ROOT}/.venv/bin:${PATH}"
fi

export PYTHONPATH="${REPO_ROOT}:${PYTHONPATH:-}"
CLI="trace"
DB="${REPO_ROOT}/mock_data/test_workspace.sqlite"

echo "================================================================================"
echo "          Starting TRACE CLI Comprehensive Mock Data Test Suite"
echo "================================================================================"

# Clean up any leftover test database
rm -f "${DB}"

# 1. Main Banner & Help
echo -e "\n--> 1. Testing 'trace' (Banner & Help Display)"
${CLI}

# 2. Adapter List
echo -e "\n--> 2. Testing 'trace adapter list'"
${CLI} adapter list

# 3. Policy Validation
echo -e "\n--> 3. Testing 'trace policy validate'"
${CLI} policy validate mock_data/compliance_policy.policy

# 4. Ingestion of Framework Traces
echo -e "\n--> 4. Testing 'trace ingest' (Normal Framework Traces)"
${CLI} ingest mock_data/events.json --framework mcp --db "${DB}"

# 5. Ingestion of Anomalous Traces
echo -e "\n--> 5. Testing 'trace ingest' (Anomalous Traces for Verification)"
${CLI} ingest mock_data/anomalous_events.json --framework mcp --db "${DB}"

# 6. Benchmark Ingestion & Automated Model Inference
echo -e "\n--> 6. Testing 'trace ingest-benchmark' with Automated Model Training"
${CLI} ingest-benchmark mock_data/benchmark_trajectories.json --dataset swebench --db "${DB}" --train

# 7. Model Training (ALERGIA inference on ingested research-agent traces)
echo -e "\n--> 7. Testing 'trace train' for research-agent"
${CLI} train --agent-id research-agent --engine native-alergia --heuristic alergia --alpha 0.05 --db "${DB}"

# 8. Model Training for security-agent
echo -e "\n--> 8. Testing 'trace train' for security-agent"
${CLI} train --agent-id security-agent --engine native-alergia --heuristic alergia --alpha 0.05 --db "${DB}"

# 9. Model List
echo -e "\n--> 9. Testing 'trace model list'"
${CLI} model list --db "${DB}"

# Extract a model ID for inspection and promotion
MODEL_ID=$(${CLI} model list --db "${DB}" | grep "research-agent" | head -n 1 | awk '{print $2}')
echo "Discovered Model ID for research-agent: ${MODEL_ID}"

# 10. Model Inspection
echo -e "\n--> 10. Testing 'trace model inspect'"
${CLI} model inspect "${MODEL_ID}" --db "${DB}"

# 11. Model Validation
echo -e "\n--> 11. Testing 'trace validate'"
${CLI} validate --model-id "${MODEL_ID}" --db "${DB}"

# 12. Model Promotion
echo -e "\n--> 12. Testing 'trace model promote'"
${CLI} model promote "${MODEL_ID}" --activate --db "${DB}"

# 13. Trace Replay
echo -e "\n--> 13. Testing 'trace replay'"
${CLI} replay --trace-id trace-research-001 --db "${DB}"

# 14. Trace Verification (Normal Trace against active model)
echo -e "\n--> 14. Testing 'trace verify' (Normal Trace: Expected PASS)"
${CLI} verify --trace-id trace-research-001 --db "${DB}"

# 15. Trace Verification (Anomalous Trace with Policy: Expected FAIL / Violation Alert)
echo -e "\n--> 15. Testing 'trace verify' (Anomalous Trace with Policy Violation)"
set +e
${CLI} verify --trace-id trace-violation-001 --policy mock_data/compliance_policy.policy --db "${DB}"
VERIFY_RET=$?
set -e
if [ ${VERIFY_RET} -ne 0 ]; then
    echo "✔ Successfully detected expected policy violation!"
else
    echo "✖ Expected violation was not flagged!"
    exit 1
fi

# 16. Human-in-the-Loop Feedback Recording
echo -e "\n--> 16. Testing 'trace feedback record'"
${CLI} feedback record \
  --violation-id viol-mock-001 \
  --type approve \
  --reviewer lead-secops \
  --comment "Verified authorized security audit exception" \
  --db "${DB}"

# 17. Human-in-the-Loop Feedback List
echo -e "\n--> 17. Testing 'trace feedback list'"
${CLI} feedback list --db "${DB}"

# 18. Empirical Benchmarks Evaluation (RQ3 Latency & RQ2 Recovery)
echo -e "\n--> 18. Testing 'trace benchmark run --rq 3'"
${CLI} benchmark run --rq 3

echo -e "\n--> 19. Testing 'trace benchmark run --rq 2'"
${CLI} benchmark run --rq 2

# Cleanup
rm -f "${DB}"

echo "================================================================================"
echo "✔ ALL 19 TRACE CLI TEST STEPS WITH MOCK DATA COMPLETED SUCCESSFULLY!"
echo "================================================================================"
