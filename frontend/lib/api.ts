const BASE_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface AnalysisResult {
  transcript: string;
  age_group: string;
  annotated_image: string | null;
  wound_conditions: string[];
  risk_score: number;
  risk_level: string;
  detected_symptoms: string[];
  recommended_specialties: { specialty: string; score: number }[];
  best_provider: string | null;
  optimization_raw: string;
  enriched_symptoms: string;
  auto_urgent: boolean;
  numeric_age_used: number;
}

export async function analyzeAll(
  audioBlob: Blob,
  image: File | null,
  budget: number | null,
  location: string | null
): Promise<AnalysisResult> {
  const form = new FormData();
  form.append("audio", audioBlob, "recording.wav");
  if (image) form.append("image", image);
  if (budget !== null) form.append("budget", String(budget));
  if (location) form.append("location", location);

  const res = await fetch(`${BASE_URL}/analyze-full`, {
    method: "POST",
    body: form,
  });

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `Erreur serveur: ${res.status}`);
  }

  return res.json();
}

export async function reverseGeocode(lat: number, lon: number): Promise<string> {
  const res = await fetch(
    `https://nominatim.openstreetmap.org/reverse?lat=${lat}&lon=${lon}&format=json`,
    { headers: { "Accept-Language": "fr" } }
  );
  const data = await res.json();
  return (
    data.address?.city ||
    data.address?.town ||
    data.address?.village ||
    data.address?.state ||
    "Localisation inconnue"
  );
}