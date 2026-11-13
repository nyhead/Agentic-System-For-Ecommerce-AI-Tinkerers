# Agentic System for Ecommerce

A Shopify merchant turns a product photo and rough notes into a draft listing, launch copy, and a follow-up recommendation.

| Owner | Branch | Demo deliverable |
| --- | --- | --- |
| Nihad | `product-agent` | Turn a photo and notes into a Shopify dev-store draft with an image. Own integration and the demo screen. |
| Ibrahim | `marketing-agent` | Generate an SEO title, meta description, and two social ad drafts for review. |
| Musa | `analytics-agent` | Show sessions, orders, conversion rate, and one suggested next action using labeled synthetic metrics. |

## Shared inputs

- Start with [the product fixture](fixtures/product.json). Keep `productId` stable across agents. The live listing fills in `shopifyProductId` and `imageUrl`.
- Marketing returns `seoTitle`, `metaDescription`, and two `socialAds` entries, each with `channel` and `copy`.
- Analytics joins [the sample metrics](fixtures/metrics.json) by `productId`. Divide orders by sessions and show no rate when sessions are zero. Keep synthetic data labeled.
- Use local environment variables for credentials. Create the draft in a dev store with Shopify's [`productCreate` mutation](https://shopify.dev/docs/api/admin-graphql/latest/mutations/productCreate), using `DRAFT` status and `write_products` access.

## Team workflow

Open one issue and one PR per workstream. Branch from `main` and post blockers in the team chat. Agree on fixture changes before merging. Run all three stages with the sample product, then verify the live Shopify draft and its photo in Admin. Rehearse the complete flow before the [18:00 Prague demo](https://luma.com/6rgvg844).
