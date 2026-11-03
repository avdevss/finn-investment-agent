// Fetches a transcript PDF and extracts its text. A scanned PDF stops the run; no OCR.
import "server-only";
import { extractText, getDocumentProxy } from "unpdf";
import { LiveError } from "./errors";
import { BSE_PDF_HEADERS, CHROME_UA, get } from "./http";

const MIN_TEXT_CHARS = 500;
const MAX_PDF_BYTES = 25 * 1024 * 1024;

export async function transcriptText(url: string, signal: AbortSignal): Promise<{ text: string; pages: number }> {
  const headers = new URL(url).host.endsWith("bseindia.com") ? BSE_PDF_HEADERS : { "User-Agent": CHROME_UA };
  let bytes: Uint8Array;
  try {
    bytes = new Uint8Array(await (await get(url, headers, signal)).arrayBuffer());
  } catch (e) {
    if (signal.aborted) throw e;
    throw new LiveError("blocked", "The transcript PDF could not be downloaded from its source. Try again later.");
  }
  if (bytes.byteLength > MAX_PDF_BYTES) {
    throw new LiveError("failed", "The transcript PDF is too large to read inside the time limit.");
  }
  // A filing link that answers with an HTML page instead of a PDF.
  if (String.fromCharCode(...bytes.slice(0, 4)) !== "%PDF") {
    throw new LiveError("blocked", "The transcript link did not return a PDF.");
  }

  const pdf = await getDocumentProxy(bytes);
  const { totalPages, text } = await extractText(pdf, { mergePages: true });
  if (text.replace(/\s/g, "").length < MIN_TEXT_CHARS) {
    throw new LiveError("scanned",
      "The latest transcript is a scanned image with no text layer. FINN does not OCR, so this company cannot be analysed live.");
  }
  return { text, pages: totalPages };
}
