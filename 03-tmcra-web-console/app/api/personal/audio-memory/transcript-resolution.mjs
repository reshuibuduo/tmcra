const MAX_COMPARISON_CHARACTERS = 5_000;
const CONFIDENCE_BANDS = new Set(["high", "medium", "low"]);

export async function resolveTranscriptCandidates({ local, remote, protectedTerms = [] }) {
  const localCandidate = await candidateValue(local, "local");
  const remoteCandidate = await candidateValue(remote, "remote");
  if (!remoteCandidate) throw new TypeError("A remote transcript candidate is required.");
  const terms = [...new Set(protectedTerms.map((value) => String(value ?? "").trim()).filter(Boolean))]
    .slice(0, 50);
  if (!localCandidate) {
    return resolutionValue({
      status: "resolved",
      selectedSource: "remote",
      confidenceBand: "medium",
      similarity: null,
      reasons: ["remote_only"],
      criticalConflicts: [],
      finalTranscript: remoteCandidate.text,
      localCandidate: null,
      remoteCandidate,
    });
  }

  const localComparable = normalizeComparable(localCandidate.text);
  const remoteComparable = normalizeComparable(remoteCandidate.text);
  const similarity = transcriptSimilarity(localComparable, remoteComparable);
  const criticalConflicts = criticalTranscriptConflicts(
    localCandidate.text,
    remoteCandidate.text,
    terms,
  );
  const numericConflict = criticalConflicts.some((value) => value.kind === "numeric_or_time");
  const protectedConflict = criticalConflicts.find((value) => value.kind === "protected_term");

  if (localComparable === remoteComparable) {
    return resolutionValue({
      status: "resolved",
      selectedSource: "agreement",
      confidenceBand: "high",
      similarity: 1,
      reasons: ["normalized_exact_match"],
      criticalConflicts: [],
      finalTranscript: remoteCandidate.text,
      localCandidate,
      remoteCandidate,
    });
  }
  if (numericConflict) {
    return reviewRequired(localCandidate, remoteCandidate, similarity, criticalConflicts, [
      "critical_number_or_time_conflict",
    ]);
  }
  if (protectedConflict) {
    const localContains = protectedConflict.local === "present";
    const remoteContains = protectedConflict.remote === "present";
    if (localContains !== remoteContains) {
      const selected = localContains ? localCandidate : remoteCandidate;
      return resolutionValue({
        status: "resolved",
        selectedSource: localContains ? "local" : "remote",
        confidenceBand: "medium",
        similarity,
        reasons: ["confirmed_term_preserved"],
        criticalConflicts,
        finalTranscript: selected.text,
        localCandidate,
        remoteCandidate,
      });
    }
  }
  if (similarity >= 0.92) {
    return resolutionValue({
      status: "resolved",
      selectedSource: "agreement",
      confidenceBand: "high",
      similarity,
      reasons: ["near_match_remote_punctuation_preferred"],
      criticalConflicts,
      finalTranscript: remoteCandidate.text,
      localCandidate,
      remoteCandidate,
    });
  }
  if (similarity >= 0.74) {
    return resolutionValue({
      status: "resolved",
      selectedSource: "remote",
      confidenceBand: "medium",
      similarity,
      reasons: ["moderate_match_remote_accuracy_preferred"],
      criticalConflicts,
      finalTranscript: remoteCandidate.text,
      localCandidate,
      remoteCandidate,
    });
  }
  return reviewRequired(localCandidate, remoteCandidate, similarity, criticalConflicts, [
    "large_transcript_divergence",
  ]);
}

export function transcriptSimilarity(left, right) {
  const a = String(left ?? "");
  const b = String(right ?? "");
  if (a === b) return 1;
  if (!a || !b) return 0;
  if (Math.max(a.length, b.length) > MAX_COMPARISON_CHARACTERS) {
    return trigramDice(a, b);
  }
  const longer = a.length >= b.length ? a : b;
  const shorter = a.length >= b.length ? b : a;
  let previous = Array.from({ length: shorter.length + 1 }, (_, index) => index);
  for (let row = 1; row <= longer.length; row += 1) {
    const current = [row];
    for (let column = 1; column <= shorter.length; column += 1) {
      current[column] = Math.min(
        current[column - 1] + 1,
        previous[column] + 1,
        previous[column - 1] + (longer[row - 1] === shorter[column - 1] ? 0 : 1),
      );
    }
    previous = current;
  }
  return rounded(1 - previous[shorter.length] / longer.length);
}

export function criticalTranscriptConflicts(localText, remoteText, protectedTerms = []) {
  const conflicts = [];
  const localCritical = criticalTokens(localText);
  const remoteCritical = criticalTokens(remoteText);
  if (!setEquals(localCritical, remoteCritical)) {
    conflicts.push({
      kind: "numeric_or_time",
      local: [...localCritical].sort().join(" | ") || "missing",
      remote: [...remoteCritical].sort().join(" | ") || "missing",
    });
  }
  const normalizedLocal = normalizeComparable(localText);
  const normalizedRemote = normalizeComparable(remoteText);
  for (const rawTerm of protectedTerms) {
    const term = normalizeComparable(rawTerm);
    if (!term) continue;
    const localPresent = normalizedLocal.includes(term);
    const remotePresent = normalizedRemote.includes(term);
    if (localPresent !== remotePresent) {
      conflicts.push({
        kind: "protected_term",
        term: String(rawTerm).trim().slice(0, 80),
        local: localPresent ? "present" : "missing",
        remote: remotePresent ? "present" : "missing",
      });
    }
  }
  return conflicts.slice(0, 30);
}

export function normalizeComparable(value) {
  return String(value ?? "")
    .normalize("NFKC")
    .toLocaleLowerCase("und")
    .replace(/[\p{P}\p{S}\s]/gu, "");
}

function criticalTokens(value) {
  const text = String(value ?? "").normalize("NFKC");
  const tokens = new Set();
  for (const match of text.matchAll(/\d+(?:[.:：]\d+)?/gu)) tokens.add(match[0].replace("：", ":"));
  for (const match of text.matchAll(/[零〇一二两三四五六七八九十百千万]+(?:年|月|日|号|点|时|分|秒|周|星期|元|块|次|个|路|楼|室)/gu)) {
    tokens.add(match[0]);
  }
  return tokens;
}

async function candidateValue(value, source) {
  if (!value) return null;
  const text = String(value.text ?? "").trim();
  if (!text || text.length > 50_000 || /\u0000/u.test(text)) {
    throw new TypeError(`${source} transcript is invalid.`);
  }
  const model = optionalText(value.model, 200);
  const provider = optionalText(value.provider, 80);
  const confidence = value.confidence === undefined || value.confidence === null
    ? null
    : Number(value.confidence);
  if (confidence !== null && (!Number.isFinite(confidence) || confidence < 0 || confidence > 1)) {
    throw new TypeError(`${source} transcript confidence is invalid.`);
  }
  return {
    source,
    text,
    sha256: await sha256Hex(text),
    model,
    provider,
    confidence,
  };
}

function reviewRequired(localCandidate, remoteCandidate, similarity, criticalConflicts, reasons) {
  return resolutionValue({
    status: "review_required",
    selectedSource: "none",
    confidenceBand: "low",
    similarity,
    reasons,
    criticalConflicts,
    finalTranscript: null,
    localCandidate,
    remoteCandidate,
  });
}

function resolutionValue(value) {
  if (!CONFIDENCE_BANDS.has(value.confidenceBand)) throw new TypeError("Invalid confidence band.");
  return {
    status: value.status,
    selectedSource: value.selectedSource,
    confidenceBand: value.confidenceBand,
    similarity: value.similarity === null ? null : rounded(value.similarity),
    reasons: value.reasons,
    criticalConflicts: value.criticalConflicts,
    finalTranscript: value.finalTranscript,
    localCandidate: value.localCandidate,
    remoteCandidate: value.remoteCandidate,
  };
}

function trigramDice(left, right) {
  const grams = (value) => {
    const result = new Map();
    for (let index = 0; index < value.length - 2; index += 1) {
      const gram = value.slice(index, index + 3);
      result.set(gram, (result.get(gram) ?? 0) + 1);
    }
    return result;
  };
  const a = grams(left);
  const b = grams(right);
  let overlap = 0;
  for (const [gram, count] of a) overlap += Math.min(count, b.get(gram) ?? 0);
  const total = [...a.values()].reduce((sum, value) => sum + value, 0)
    + [...b.values()].reduce((sum, value) => sum + value, 0);
  return total ? rounded((2 * overlap) / total) : 0;
}

function setEquals(left, right) {
  return left.size === right.size && [...left].every((value) => right.has(value));
}

function optionalText(value, maximum) {
  if (value === undefined || value === null || value === "") return null;
  const clean = String(value).trim();
  if (!clean || clean.length > maximum || /\u0000/u.test(clean)) throw new TypeError("Candidate field is invalid.");
  return clean;
}

function rounded(value) {
  return Math.round(Math.max(0, Math.min(1, Number(value))) * 10_000) / 10_000;
}

async function sha256Hex(value) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value));
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

