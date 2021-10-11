library InfantFactors version '1.0.000'

using FHIR version '4.0.0'

include FHIRHelpers version '4.0.0' called FHIRHelpers

codesystem "Location-Type": 'http://terminology.hl7.org/ValueSet/v3-ServiceDeliveryLocationRoleType'
codesystem "LOINC": 'http://loinc.org'
codesystem "SNOMED": 'http://snomed.info/sct'

valueset "Special Care Locations Set": 'http://test-url.com/fhir/ValueSet/special-care-locations'
valueset "Low Birth Weight Set": 'http://test-url.com/fhir/ValueSet/low-birth-weight'
valueset "Blood Transfusion Set": 'http://test-url.com/fhir/ValueSet/blood-transfusion'
valueset "Dopamine Set": 'http://test-url.com/fhir/ValueSet/dopamine'
valueset "Topical Iodine Set": 'http://test-url.com/fhir/ValueSet/topical-iodine'
valueset "Hypothyroxinemia Set": 'http://test-url.com/fhir/ValueSet/hypothyroxinemia'
valueset "Hypoxemia Set": 'http://test-url.com/fhir/ValueSet/hypoxemia'
valueset "Hypothalamic/Pituitary Axis Set": 'http://test-url.com/fhir/ValueSet/hypothalamic-pituitary-axis'
valueset "Immature Liver Enzymes Set": 'http://test-url.com/fhir/ValueSet/immature-liver-enzymes'
valueset "Immature Renal System Set": 'http://test-url.com/fhir/ValueSet/immature-renal-system'
valueset "Iodine Deficiency Set": 'http://test-url.com/fhir/ValueSet/iodine-deficiency'
valueset "Liver Disease Set": 'http://test-url.com/fhir/ValueSet/liver-disease'
valueset "NBS Other Conditions Set": 'http://test-url.com/fhir/ValueSet/nbs-other-conditions'
valueset "Steroid Treatment Set": 'http://test-url.com/fhir/ValueSet/steroid-treatment'
valueset "Systemic Antibiotics Set": 'http://test-url.com/fhir/ValueSet/systemic-antibiotics'
valueset "Bowel Obstruction Set": 'http://test-url.com/fhir/ValueSet/bowel-obstruction'
valueset "Thoracic Surgery Thymectomy Set": 'http://test-url.com/fhir/ValueSet/thoracic-surger-thymectomy'
valueset "Immunosuppressive Therapy Set": 'http://test-url.com/fhir/ValueSet/immunosuppressive-therapy'
valueset "TPN Set": 'http://test-url.com/fhir/ValueSet/tpn'
valueset "Lactose-Free Diet Set": 'http://test-url.com/fhir/ValueSet/lactose-free-diet'
valueset "Low Protein Diet Set": 'http://test-url.com/fhir/ValueSet/low-protein-diet'

code "NICU Code": 'PEDNICU' from "Location-Type" display 'Pediatric neonatal intensive care unit'
code "Infant Factors Narrative Code": '67703-9' from "LOINC"
code "Acute Condition Code": '24484000' from "SNOMED" display 'Severe'
code "Oral Route Code": '26643006' from "SNOMED" display 'Oral use'

context Patient

define "Infant in NICU at time of specimen collection":
    [Encounter] E
        with [Location: "NICU Code"] L
            such that E.location.single().location.reference.value = 'Location/' + L.id.value
                return {
                    "Type": '\"Encounter\",',
                    "ID": '\"' + E.id.value + '\"'
                }

define "Infant in special care setting (other than ICU) at time of specimen collection":
    [Encounter] E
        with [Location: "Special Care Locations Set"] L
            such that E.location.single().location.reference.value = 'Location/' + L.id.value
                return {
                    "Type": '\"Encounter\",',
                    "ID": '\"' + E.id.value + '\"'
                }

define "Preterm/Low birth weight (LBW)":
    [Condition: "Low Birth Weight Set"] C
    return {
        "Type": '\"Condition\",',
        "ID": '\"' + C.id.value + '\"'
    }

define "Any blood product transfusion (including ECLS/ECMO)":
    [Procedure: "Blood Transfusion Set"] P
    return {
        "Type": '\"Procedure\",',
        "ID": '\"' + P.id.value + '\"'
    }

define "Dopamine":
    [MedicationRequest: "Dopamine Set"] M
    return {
        "Type": '\"MedicationRequest\",',
        "ID": '\"' + M.id.value + '\"'
    }

define "Topical iodine":
    [MedicationRequest: "Topical Iodine Set"] M
    return {
        "Type": '\"MedicationRequest\",',
        "ID": '\"' + M.id.value + '\"'
    }

define "Acute illness":
    [Condition] C
    where exists(
        C.severity.coding Coding
        where FHIRHelpers.ToCode(Coding) ~ "Acute Condition Code"
    )
    return {
        "Type": '\"Condition\",',
        "ID": '\"' + C.id.value + '\"'
    }

define "Hypothyroxinemia of preterm birth":
    [Condition: "Hypothyroxinemia Set"] C
    return {
        "Type": '\"Condition\",',
        "ID": '\"' + C.id.value + '\"'
    }

define "Significant hypoxia":
    [Condition: "Hypoxemia Set"] C
    return {
        "Type": '\"Condition\",',
        "ID": '\"' + C.id.value + '\"'
    }

define "Immature hypothalamic/pituitary axis":
    [Condition: "Hypothalamic/Pituitary Axis Set"] C
    return {
        "Type": '\"Condition\",',
        "ID": '\"' + C.id.value + '\"'
    }

define "Immature liver enzymes":
    [Observation: "Immature Liver Enzymes Set"] O
    return {
        "Type": '\"Observation\",',
        "ID": '\"' + O.id.value + '\"'
    }

define "Immature renal system":
    [Condition: "Immature Renal System Set"] C
    return {
        "Type": '\"Condition\",',
        "ID": '\"' + C.id.value + '\"'
    }

define "Iodine deficiency":
    [Condition: "Iodine Deficiency Set"] C
    return {
        "Type": '\"Condition\",',
        "ID": '\"' + C.id.value + '\"'
    }

define "Liver disease":
    [Condition: "Liver Disease Set"] C
    return {
        "Type": '\"Condition\",',
        "ID": '\"' + C.id.value + '\"'
    }

define "Other conditions, such as biliary atresia, intestinal perforation, abdominal wall defects, septicemia, CMV, renal failure, T21, T18, T13":
    [Condition: "NBS Other Conditions Set"] C
    return {
        "Type": '\"Condition\",',
        "ID": '\"' + C.id.value + '\"'
    }

define "Parenteral steroid treatment":
    [MedicationRequest: "Steroid Treatment Set"] M
    where exists(
        M.dosageInstruction.route.coding Route
        where FHIRHelpers.ToCode(Route) != "Oral Route Code"
    )
    return {
        "Type": '\"MedicationRequest\",',
        "ID": '\"' + M.id.value + '\"'
    }

define "Systemic antibiotics before newborn screening":
    [MedicationRequest: "Systemic Antibiotics Set"] M
    where exists(
        M.dosageInstruction.route.coding Route
        where FHIRHelpers.ToCode(Route) = "Oral Route Code"
    ) and M.authoredOn before Now()
    return {
        "Type": '\"MedicationRequest\",',
        "ID": '\"' + M.id.value + '\"'
    }

define "Meconium ileus or other bowel obstruction":
    [Condition: "Bowel Obstruction Set"] C
    return {
        "Type": '\"Condition\",',
        "ID": '\"' + C.id.value + '\"'
    }

define "Thoracic surgery involving thymectomy":
    [Procedure: "Thoracic Surgery Thymectomy Set"] P
    return {
        "Type": '\"Procedure\",',
        "ID": '\"' + P.id.value + '\"'
    }

define "Immunosuppressive therapy of baby or mother":
    [Procedure: "Immunosuppressive Therapy Set"] P
    return {
        "Type": '\"Procedure\",',
        "ID": '\"' + P.id.value + '\"'
    }

define "Total parenteral nutrition (TPN) or similar feeding":
    [Observation: "TPN Set"] O
    return {
        "Type": '\"Observation\",',
        "ID": '\"' + O.id.value + '\"'
    }

define "Special lactose-free diet":
    [Observation: "Lactose-Free Diet Set"] O
    return {
        "Type": '\"Observation\",',
        "ID": '\"' + O.id.value + '\"'
    }

define "Special low protein diet":
    [Observation: "Low Protein Diet Set"] O
    return {
        "Type": '\"Observation\",',
        "ID": '\"' + O.id.value + '\"'
    }

define "The narrative for infant factors that could impact NBS interpretation.":
    [Observation: "Infant Factors Narrative Code"] O
    return {
        "Type": '\"Observation\",',
        "ID": '\"' + O.id.value + '\",',
        "Narrative": '\"' + O.value + '\"'
    }

define "The date of last blood product transfusion":
    [Procedure : "Blood Transfusion Set"] P
    return {
        "Type": '\"Procedure\",',
        "ID": '\"' + P.id + '\",',
        "Date": '\"' + P.performed + '\"'
    }