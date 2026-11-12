import { afterEach, describe, expect, it } from "vitest";

import { stubDownloads } from "../test/download";
import { saveBlob } from "./download";

describe("saveBlob", () => {
  let stub: ReturnType<typeof stubDownloads> | null = null;

  afterEach(() => {
    stub?.restore();
    stub = null;
  });

  it("指定の名前で保存させ、一時的な URL を解放し、文書に部品を残さない", () => {
    stub = stubDownloads();
    const blob = new Blob(["WBS ID\n1\n"], { type: "text/csv" });

    saveBlob(blob, "project-3-wbs.csv");

    expect(stub.saved).toEqual([{ blob, filename: "project-3-wbs.csv", revoked: true }]);
    expect(document.querySelectorAll("a[download]")).toHaveLength(0);
  });
});
