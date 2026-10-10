// Which pages of the quote are sent and which are left out, with their page numbers in the
// original quote across every file the household chose. The server marks a reading with any
// left-out page as incomplete, so the checks that need the whole quote ask first.

export const REASONS = { over_limit: "over the page limit", too_large: "too large to send", unreadable: "unreadable" };

export class PagePlan {
  constructor(maxPages) {
    this.maxPages = maxPages;
    this.pages = [];    // [{ page, blob }] in page order
    this.omitted = [];  // [{ page, reason }]
    this.total = 0;
  }

  room() {
    return Math.max(0, this.maxPages - this.pages.length);
  }

  // The next page of the quote: kept with its image, or left out with a reason.
  keep(blob) {
    this.total += 1;
    this.pages.push({ page: this.total, blob });
    return this.total;
  }

  omit(reason) {
    if (!(reason in REASONS)) throw new Error(`unknown reason ${reason}`);
    this.total += 1;
    this.omitted.push({ page: this.total, reason });
    return this.total;
  }

  // What POST /jobs needs to know.
  request() {
    return {
      page_count: this.pages.length,
      page_numbers: this.pages.map((p) => p.page),
      total_pages: this.total,
      omitted: this.omitted.map((o) => ({ page: o.page, reason: o.reason })),
    };
  }
}
