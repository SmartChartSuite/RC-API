#!/usr/bin/env bash
export API_DOCS=true
export CQF_RULER_R4=https://dev.heat.icl.gtri.org/cqf-ruler-r4/fhir/
export DOCS_PREPEND_URL=
export EXTERNAL_FHIR_SERVER_URL=https://smartchartsuite.dev.heat.icl.gtri.org/fhir-proxy/
export EXTERNAL_FHIR_SERVER_AUTH='Bearer 12345'
export LOG_LEVEL=DEBUG
export NLPAAS_URL=https://smartchartsuite.dev.heat.icl.gtri.org/nlpaas
export DEPLOY_URL=http://localhost:8080

hypercorn main:app --reload