
library temporary_retrieve version '1.0'

using FHIR version '3.0.0'

include FHIRHelpers version '3.0.0' called FHIRHelpers

codesystem "LOINC": 'http://loinc.org'
codesystem "SNOMED": '2.16.840.1.113883.6.96'
codesystem "RxNorm": 'http://www.nlm.nih.gov/research/umls/rxnorm'
codesystem "CPT": 'http://www.ama-assn.org/go/cpt'
codesystem "ICD9": '2.16.840.1.113883.6.42'
codesystem "ICD10": '2.16.840.1.113883.6.3'
codesystem USCoreEthnicitySystem: '2.16.840.1.113883.6.238' 
codesystem RelationshipType: '2.16.840.1.113883.4.642.3.449'

context Patient

define "aphasia_structured_concepts": Concept {
Code '87486003' from "SNOMED"
}
define "aphasia_structured": 
[Condition: Code in "aphasia_structured_concepts"]
define "coma_structured_concepts": Concept {
Code '371632003' from "SNOMED"
}
define "coma_structured": 
[Condition: Code in "coma_structured_concepts"]
define "wbc_structured_concepts": Concept {
Code '26464-8' from "LOINC", 
Code '6690-2' from "LOINC", 
Code '804-5' from "LOINC", 
Code '49498-9' from "LOINC"
}
define "wbc_structured": 
[Observation: Code in "wbc_structured_concepts"]
define "hydroxyurea_concepts": Concept {
Code '5552' from "RxNorm"
}
define "hydroxyurea": 
[MedicationStatement: Code in "hydroxyurea_concepts"]
define "unconscious_structured_concepts": Concept {
Code '418107008' from "SNOMED", 
Code '419045004' from "SNOMED", 
Code '32834005' from "SNOMED", 
Code '40863000' from "SNOMED", 
Code '7862002' from "SNOMED", 
Code '271594007' from "SNOMED"
}
define "unconscious_structured": 
[Condition: Code in "unconscious_structured_concepts"]
define "avascular_necrosis_concepts": Concept {
Code '86217007' from "SNOMED"
}
define "avascular_necrosis": 
[Condition: Code in "avascular_necrosis_concepts"]
define "body_weight_snomed_concepts": Concept {
Code '4653018224' from "SNOMED"
}
define "body_weight_snomed": 
[Observation: Code in "body_weight_snomed_concepts"]
define "date_transfusion_blood_component_concepts": Concept {
Code '36430' from "CPT", 
Code '36440' from "CPT"
}
define "date_transfusion_blood_component": 
[Procedure: Code in "date_transfusion_blood_component_concepts"]
define "fh_sickle_cell_trait_concepts": Concept {
Code '160321003' from "SNOMED", 
Code '72321000119107' from "SNOMED", 
Code '10759401000119105' from "SNOMED"
}
define "fh_sickle_cell_trait": 
[Condition: Code in "fh_sickle_cell_trait_concepts"]
define "thigh_ulcer_concepts": Concept {
Code '428194005' from "SNOMED"
}
define "thigh_ulcer": 
[Condition: Code in "thigh_ulcer_concepts"]
define "osteonecrosis_concepts": Concept {
Code '240196003' from "SNOMED"
}
define "osteonecrosis": 
[Condition: Code in "osteonecrosis_concepts"]
define "rbc_structured_concepts": Concept {
Code '789-8' from "LOINC"
}
define "rbc_structured": 
[Observation: Code in "rbc_structured_concepts"]
define "body_weight_concepts": Concept {
Code '29463-7' from "LOINC"
}
define "body_weight": 
[Observation: Code in "body_weight_concepts"]
define "arthropathy_concepts": Concept {
Code '709142003' from "SNOMED"
}
define "arthropathy": 
[Condition: Code in "arthropathy_concepts"]
define "ankle_ulcer_concepts": Concept {
Code '419193008' from "SNOMED"
}
define "ankle_ulcer": 
[Condition: Code in "ankle_ulcer_concepts"]
define "leg_ulcer_concepts": Concept {
Code '95344007' from "SNOMED"
}
define "leg_ulcer": 
[Condition: Code in "leg_ulcer_concepts"]
define "morphine_concepts": Concept {
Code '7052' from "RxNorm"
}
define "morphine": 
[MedicationStatement: Code in "morphine_concepts"]
define "asthenia_structured_concepts": Concept {
Code '13791008' from "SNOMED"
}
define "asthenia_structured": 
[Condition: Code in "asthenia_structured_concepts"]
define "seizure_structured_concepts": Concept {
Code '91175000' from "SNOMED"
}
define "seizure_structured": 
[Condition: Code in "seizure_structured_concepts"]
define "body_weight_loinc_concepts": Concept {
Code '29463-7' from "LOINC"
}
define "body_weight_loinc": 
[Observation: Code in "body_weight_loinc_concepts"]
define "meperidine_concepts": Concept {
Code '6754' from "RxNorm"
}
define "meperidine": 
[MedicationStatement: Code in "meperidine_concepts"]
define "has_transfusion_reaction_history_concepts": Concept {
Code '7831000175106' from "SNOMED"
}
define "has_transfusion_reaction_history": 
[Condition: Code in "has_transfusion_reaction_history_concepts"]
define "shin_ulcer_concepts": Concept {
Code '308908006' from "SNOMED"
}
define "shin_ulcer": 
[Condition: Code in "shin_ulcer_concepts"]
define "has_sickle_cell_trait_structured_concepts": Concept {
Code '16402000' from "SNOMED", 
Code '442018007' from "SNOMED"
}
define "has_sickle_cell_trait_structured": 
[Condition: Code in "has_sickle_cell_trait_structured_concepts"]
define "has_transfusion_history_concepts": Concept {
Code '161664006' from "SNOMED"
}
define "has_transfusion_history": 
[Condition: Code in "has_transfusion_history_concepts"]
define "date_transfusion_blood_product_concepts": Concept {
Code '116859006' from "SNOMED"
}
define "date_transfusion_blood_product": 
[Procedure: Code in "date_transfusion_blood_product_concepts"]
define "levorphanol_concepts": Concept {
Code '6378' from "RxNorm"
}
define "levorphanol": 
[MedicationStatement: Code in "levorphanol_concepts"]
define "fh_sickle_cell_anemia_concepts": Concept {
Code '160320002' from "SNOMED"
}
define "fh_sickle_cell_anemia": 
[Condition: Code in "fh_sickle_cell_anemia_concepts"]
define "glutamine_concepts": Concept {
Code '1928936' from "RxNorm"
}
define "glutamine": 
[MedicationStatement: Code in "glutamine_concepts"]
define "headache_structured_concepts": Concept {
Code '735938006' from "SNOMED", 
Code '25064002' from "SNOMED"
}
define "headache_structured": 
[Condition: Code in "headache_structured_concepts"]

