export type User = { id: string; username: string; role: 'admin' | 'readonly'; must_change_password?: boolean }

export type Platform = {
  id: number; province: string; city: string; name: string; operator: string; source_url: string;
  canonical_url: string; url_status: string; enabled: boolean; render_mode: string; adapter: string;
  source_role: string; onboarding_status: string; legal_review_status: string; rate_limit: number;
  max_concurrency: number; active_items: number; collection_count: number; notes: string;
  last_run?: { status: string; coverage: string; started_at?: string; finished_at?: string; pages: number; items: number; errors: number } | null
}

export type Task = {
  id: string; run_id?: string; mode: string; platform_ids: number[]; max_pages?: number; status: string; requested_by: string;
  created_at: string; started_at?: string; finished_at?: string; cancel_requested: boolean; error?: string
}

export type CatalogItem = {
  id: string; platform_name: string; kind: string; name: string; description: string; provider: string;
  product_type: string; published_at?: string; last_crawled_at?: string; source_url: string;
  confidence: number; status: string; dimensions: Record<string, string>
}
