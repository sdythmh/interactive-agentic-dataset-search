"""Place resolution for data.gov.au: hand-written boxes, place-string
normalisation words, and the optional gazetteer path.
"""

# ---- Hand-written boxes (retrieval.spatial_search) -------------------------
# [min_lon, min_lat, max_lon, max_lat], looked up by normalised place string.
BBOXES = {
    "australia": [112.9, -43.7, 153.7, -9.1],
    "victoria": [140.9, -39.2, 150.0, -33.9],
    "vic": [140.9, -39.2, 150.0, -33.9],
    "melbourne": [144.5, -38.5, 145.5, -37.4],
    "new south wales": [140.9, -37.6, 153.7, -28.1],
    "nsw": [140.9, -37.6, 153.7, -28.1],
    "sydney": [150.5, -34.2, 151.4, -33.4],
    "queensland": [137.9, -29.2, 153.6, -9.1],
    "qld": [137.9, -29.2, 153.6, -9.1],
    "brisbane": [152.6, -27.8, 153.3, -26.9],
    "western australia": [112.9, -35.2, 129.0, -13.6],
    "wa": [112.9, -35.2, 129.0, -13.6],
    "perth": [115.6, -32.6, 116.2, -31.5],
    "south australia": [129.0, -38.1, 141.0, -25.9],
    "sa": [129.0, -38.1, 141.0, -25.9],
    "adelaide": [138.4, -35.4, 139.0, -34.5],
    "tasmania": [143.8, -43.7, 148.5, -39.5],
    "tas": [143.8, -43.7, 148.5, -39.5],
    "hobart": [147.1, -43.1, 147.6, -42.7],
    "northern territory": [129.0, -26.0, 138.0, -10.9],
    "nt": [129.0, -26.0, 138.0, -10.9],
    "darwin": [130.7, -12.7, 131.2, -12.2],
    "canberra": [148.9, -35.6, 149.4, -35.1],
    "act": [148.7, -35.9, 149.4, -35.1],
    "australian capital territory": [148.7, -35.9, 149.4, -35.1],
    "gippsland": [146.0, -39.0, 150.0, -36.8],
    "great barrier reef": [142.5, -24.5, 154.0, -10.0],
    "murray darling basin": [138.0, -37.5, 152.5, -24.5],
}

# ---- Place-string normalisation --------------------------------------------
# The router writes 'Victoria, Australia' or 'Greater Melbourne'; the lookup
# tables hold 'victoria' and 'melbourne'. Used by retrieval.normalise_place()
# and expansion._norm_place(), which were two copies of the same list.
PLACE_PREFIXES = ("greater ", "city of ", "shire of ", "state of ")
PLACE_SUFFIXES = (" state", " australia", " region", " area", " council")

# Generic tail words that turn a named feature into a descriptive phrase
# ("Lachlan River catchment").
FEATURE_TAILS = ("catchment", "catchments", "system", "corridor", "reserve")

# PLACE_NORMALISE. Trailing words that name the KIND of Australian local government area
# rather than the area itself.
ADMIN_TAILS = ("shire", "city", "council", "borough", "municipality",
               "regional", "rural", "district", "corporation")

# ---- Gazetteer -------------------------------------------------------------
# Read by expansion.scheme_b_place(), which the paper configuration leaves off.
GAZETTEER_FILE = "data/gazetteer_au.json"
