"""Product agent: draft a listing from a photo and notes, create it in Shopify, publish it."""

from pydantic import BaseModel, ConfigDict, Field

from app.state import ProductRecord
from app.tools.llm import Image, LLMClient
from app.tools.shopify import ShopifyClient, create_draft_product, publish_product, upload_image

# Search results and most storefront themes cut or wrap titles beyond about 70 characters.
MAX_TITLE_LENGTH = 70
# Below the adapter's 60 s so a stuck call fails fast on stage, yet well above the 5-20 s a
# vision reply with an embedded photo usually takes.
DRAFT_TIMEOUT_SECONDS = 45.0
# Low, so the model stays close to the facts it was given instead of embellishing.
DRAFT_TEMPERATURE = 0.3

SYSTEM_PROMPT = f"""\
You write product listings for a small independent shop. The shop owner publishes what you \
write under their own name, to real customers, so every claim in it must be true. A false \
claim about a product can mislead a buyer, cause returns and damage the owner's reputation.

You get the owner's notes and, sometimes, a photo of the product. A photo shows shape, colour \
and visible features, and nothing else. It does not tell you what the product is made of, its \
exact measurements, weight or capacity, its price or stock, where it was made, or whether it \
is certified, handmade, organic or safe for a particular use. State such facts only when the \
owner's notes state them. If the notes and the photo disagree, follow the notes: the owner \
has the product in hand. If a detail is unknown, leave it out. Do not guess, estimate or hint \
at it, and never write placeholders such as [size] or TBD.

Write:
- title: the product's name as a shopper would search for it, at most {MAX_TITLE_LENGTH} \
characters, without the shop name, the price or promotional words.
- description: two to four plain sentences that a customer reads on the product page, about \
what the product is, what it looks like and what the notes say about it. Plain text only: no \
markdown, bullet points, headings, emoji or hashtags."""


class _ListingDraft(BaseModel):
    """What the model returns; the ProductRecord is built from it."""

    model_config = ConfigDict(str_strip_whitespace=True)

    title: str = Field(min_length=1, description="Product title shown to shoppers.")
    description: str = Field(min_length=1, description="Two to four plain-text sentences.")


def draft_listing(
    product_id: str,
    notes: str,
    *,
    image: Image | None = None,
    llm: LLMClient | None = None,
) -> ProductRecord:
    """Draft a title and description with the model. Writes nothing to Shopify.

    Raises ValueError when there are neither notes nor an image, and LLMError when the model
    gives no usable reply.
    """
    if not product_id.strip():
        raise ValueError("product_id is empty")
    notes = notes.strip()
    if not notes and image is None:
        raise ValueError("nothing to draft from: give the merchant's notes, a photo, or both")
    llm = llm or LLMClient(timeout=DRAFT_TIMEOUT_SECONDS)
    draft = llm.structured(
        _user_prompt(notes, has_image=image is not None),
        _ListingDraft,
        system=SYSTEM_PROMPT,
        images=[image] if image is not None else [],
        temperature=DRAFT_TEMPERATURE,
    )
    return ProductRecord(
        productId=product_id, title=_fit_title(draft.title), description=draft.description
    )


def create_draft(
    record: ProductRecord,
    *,
    image_url: str | None = None,
    photo: bytes | None = None,
    photo_filename: str = "photo.jpg",
    photo_mime_type: str = "image/jpeg",
    client: ShopifyClient | None = None,
) -> ProductRecord:
    """Create the DRAFT product in Shopify, with the image from `image_url` or `photo` bytes.

    Repeating the call for the same productId returns the existing product (the Shopify tool
    looks it up by handle). With `photo`, the bytes are staged before that lookup, so a retry
    after a failed call stages them again; the extra staged file is never attached and Shopify
    expires it. Passing back a record that already has `shopifyProductId` skips the upload.
    """
    if image_url is not None and photo is not None:
        raise ValueError("pass image_url or photo, not both")
    if photo is not None and not photo:
        raise ValueError("photo is empty")
    client = client or ShopifyClient()
    if photo is not None and record.shopifyProductId is None:
        image_url = upload_image(photo, photo_filename, photo_mime_type, client=client)
    return create_draft_product(record, image_url=image_url, client=client)


def publish(record: ProductRecord, *, client: ShopifyClient | None = None) -> ProductRecord:
    """Make the draft product ACTIVE and publish it to the Online Store.

    Call this only after the merchant has approved the listing. This module does not check
    approval; the caller owns that decision.
    """
    return publish_product(record, client=client)


def _user_prompt(notes: str, *, has_image: bool) -> str:
    source = (
        "A photo of the product is attached."
        if has_image
        else "There is no photo. Use only the notes."
    )
    if notes:
        return f"{source}\n\nThe shop owner's notes:\n<notes>\n{notes}\n</notes>"
    return f"{source}\n\nThe shop owner gave no notes. Describe only what the photo shows."


def _fit_title(title: str) -> str:
    if len(title) <= MAX_TITLE_LENGTH:
        return title
    cut = title[: MAX_TITLE_LENGTH + 1].rsplit(" ", 1)[0]
    # A title with no space early enough is cut hard rather than reduced to nothing.
    if len(cut) > MAX_TITLE_LENGTH or not cut.strip():
        cut = title[:MAX_TITLE_LENGTH]
    return cut.rstrip(" ,;:-–—")
