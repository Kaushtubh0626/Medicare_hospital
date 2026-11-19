/* Educational adult screening examples, NOT a validated clinical triage protocol.
 * Disease names are deliberately excluded from this function's inputs.
 * Lower numbers mean higher queue priority. Always take the most urgent rule.
 * Missing measurements never count as normal. Unknown symptoms default to Medium
 * for human review; Low requires explicit mild symptoms and reviewed warning signs.
 * Pediatric measurements require manual review; adult cutoffs are not applied.
 */
(function (root) {
  'use strict';
  function assessSeverity({symptoms = '', age, vitals = {}, warnings = [], warningsReviewed = false}) {
    const reasons = [], errors = [], matched = [];
    const text = symptoms.toLowerCase().replace(/[’]/g, "'");
    function flag(level, reason) { matched.push(level); reasons.push(reason); }
    const bounds = {spo2:[1,100], pulse:[1,350], respiratoryRate:[1,100], systolic:[1,300], temperature:[25,45]};
    const values = {};
    for (const [name, [min,max]] of Object.entries(bounds)) {
      const raw = vitals[name];
      if (raw === '' || raw == null) continue;
      const number = Number(raw);
      if (!Number.isFinite(number) || number < min || number > max) errors.push(`Check ${name}: expected ${min}–${max}.`);
      else values[name] = number;
    }
    // Structured current warning signs are authoritative even if text is negated.
    for (const warning of warnings) flag(1, `Current emergency warning sign: ${warning}.`);
    // Handle negation per clause: "no fever, but chest pain" still matches chest pain.
    // Ambiguous/history clauses never produce reassuring findings or Low priority.
    let ambiguous = false;
    for (const clause of text.split(/[.,;!?\n]|\bbut\b|\bhowever\b/)) {
      if (!clause.trim()) continue;
      const current = clause.replace(/\bnot breathing\b/g, 'breathing stopped').replace(/\bnot responding\b/g,'unresponsive');
      const emergency = current.match(/\b(chest pain|chest pressure|difficulty breathing|breathing difficulty|shortness of breath|cannot breathe|can't breathe|breathing stopped|unconscious|unresponsive|severe bleeding|heavy bleeding|blue lips|seizure|facial droop|slurred speech|sudden weakness)\b/);
      if (/\b(no|not|denies|denied|without|history|previous|resolved|yesterday)\b/.test(current)) {
        ambiguous = true;
        // Keyword rules cannot resolve scope of negation. Preserve a potential
        // emergency conservatively rather than hide "no fever and chest pain".
        if (emergency) flag(1, `Possible emergency phrase “${emergency[0]}” in ambiguous text: immediate staff review required.`);
        continue;
      }
      const high = current.match(/\b(fracture|broken (arm|leg|bone)|severe pain|high fever|persistent vomiting|severe abdominal pain|dehydration)\b/);
      const medium = current.match(/\b(fever|vomiting|abdominal pain|dizziness|headache)\b/);
      const low = current.match(/\b(mild cough|runny nose|minor scratch|minor cut|mild sore throat)\b/);
      if (emergency) flag(1, `Emergency symptom phrase: “${emergency[0]}”.`);
      else if (high) flag(2, `High-priority symptom phrase: “${high[0]}”.`);
      else if (medium) flag(3, `Symptom requiring review: “${medium[0]}”.`);
      else if (low) matched.push(4);
    }
    const adult = Number(age) >= 18 && age !== '' && age != null;
    if (adult) {
      // Illustrative adult warning thresholds; extremes always take precedence.
      if (values.spo2 <= 90) flag(1, 'Oxygen saturation ≤90%.');
      else if (values.spo2 < 94) flag(2, 'Oxygen saturation <94%.');
      if (values.systolic < 90) flag(1, 'Systolic blood pressure <90 mmHg.');
      else if (values.systolic >= 180) flag(2, 'Systolic blood pressure ≥180 mmHg.');
      if (values.respiratoryRate < 8 || values.respiratoryRate > 30) flag(1, 'Respiratory rate outside 8–30 breaths/min.');
      else if (values.respiratoryRate > 24) flag(2, 'Respiratory rate >24 breaths/min.');
      if (values.pulse < 40 || values.pulse > 140) flag(1, 'Pulse outside 40–140 beats/min.');
      else if (values.pulse < 50 || values.pulse > 120) flag(2, 'Pulse outside 50–120 beats/min.');
      if (values.temperature < 35 || values.temperature >= 40) flag(2, 'Temperature <35°C or ≥40°C.');
      else if (values.temperature >= 38) flag(3, 'Temperature ≥38°C.');
    }
    let severity = matched.length ? Math.min(...matched) : 3;
    if ((!adult || !warningsReviewed || ambiguous || errors.length) && severity > 3) severity = 3;
    if (!adult) reasons.push('Age is missing or under 18: adult vital thresholds are not applied. Manual assessment required.');
    if (ambiguous) reasons.push('Negated or historical text needs human interpretation; it is not evidence of low risk.');
    if (Object.keys(values).length < 5) reasons.push('Some vital signs are unavailable; missing values are not assumed normal.');
    if (!warningsReviewed) reasons.push('Review current emergency warning signs before considering Low.');
    if (!matched.length) reasons.push('No supported symptom rule matched. Medium is a provisional review priority, not reassurance.');
    if (severity === 4) reasons.push('Only supported mild symptoms matched and warning signs were reviewed. Confirm clinically.');
    return {severity, emergency: matched.includes(1), reasons, errors, ruleVersion:'educational-adult-v1'};
  }
  root.assessSeverity = assessSeverity;
  if (typeof module !== 'undefined') module.exports = {assessSeverity};
})(globalThis);
