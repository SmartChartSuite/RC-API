library demographics version '1.0'

using FHIR version '3.0.0'

include FHIRHelpers version '3.0.0' called FHIRHelpers

codesystem "LOINC": 'http://loinc.org'
codesystem "SNOMED": '2.16.840.1.113883.6.96'
codesystem "RxNorm": 'http://www.nlm.nih.gov/research/umls/rxnorm'
codesystem "CPT": 'http://www.ama-assn.org/go/cpt'
codesystem "ICD9": '2.16.840.1.113883.6.42'
codesystem "ICD10": '2.16.840.1.113883.6.3'

context Patient

define "patient_resource" : [Patient]
define "omb_race_complete_string": 'White, American Indian or Alaska Native, Asian, Shoshone, Filipino'
define "omb_race_summary": 'Mixed'
define "hispanic_or_latino_choice":
    "patient_resource" P
        return
            case
                when p.extension then (no ethnicity then 'Unknwon')
                when (condition) then (if code 2135-2 present then 'Yes')
                else 'No'

