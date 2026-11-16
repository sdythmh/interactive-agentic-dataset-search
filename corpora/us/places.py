"""Place resolution for data.gov (US): generated boxes, no gazetteer."""

# ---- Generated boxes -------------------------------------------------------
# [min_lon, min_lat, max_lon, max_lat], looked up by normalised place string.
# Generated from Natural Earth (see above); do not edit by hand.
BBOXES = {
    "alabama":              [-88.487, 30.223, -84.897, 35.01],
    "alaska":               [-179.144, 51.215, -129.989, 71.413],
    "arizona":              [-114.823, 31.327, -109.046, 37.004],
    "arkansas":             [-94.619, 33.01, -89.667, 36.5],
    "california":           [-124.41, 32.531, -114.119, 42.0],
    "colorado":             [-109.047, 36.999, -102.039, 41.0],
    "connecticut":          [-73.723, 41.001, -71.795, 42.055],
    "delaware":             [-75.789, 38.453, -75.043, 39.839],
    "district of columbia": [-77.12, 38.802, -76.909, 38.997],
    "florida":              [-87.627, 24.542, -80.032, 31.001],
    "georgia":              [-85.606, 30.362, -80.835, 35.001],
    "hawaii":               [-178.305, 18.906, -154.814, 28.402],
    "idaho":                [-117.216, 41.999, -111.05, 48.993],
    "illinois":             [-91.519, 36.978, -87.018, 42.512],
    "indiana":              [-88.112, 37.781, -84.783, 41.76],
    "iowa":                 [-96.638, 40.379, -90.14, 43.501],
    "kansas":               [-102.052, 36.999, -94.61, 40.001],
    "kentucky":             [-89.578, 36.498, -81.965, 39.147],
    "louisiana":            [-94.044, 28.928, -88.81, 33.015],
    "maine":                [-71.085, 43.083, -66.977, 47.46],
    "maryland":             [-79.488, 37.931, -75.043, 39.722],
    "massachusetts":        [-73.508, 41.239, -69.924, 42.887],
    "michigan":             [-90.413, 41.696, -82.145, 48.306],
    "minnesota":            [-97.23, 43.5, -89.498, 49.37],
    "mississippi":          [-91.658, 30.181, -88.088, 35.001],
    "missouri":             [-95.774, 35.986, -89.118, 40.619],
    "montana":              [-116.049, 44.376, -104.038, 48.993],
    "nebraska":             [-104.054, 40.0, -95.317, 43.001],
    "nevada":               [-120.001, 35.003, -114.04, 42.001],
    "new hampshire":        [-72.557, 42.7, -70.704, 45.305],
    "new jersey":           [-75.559, 38.924, -73.907, 41.357],
    "new mexico":           [-109.048, 31.327, -103.0, 37.0],
    "new york":             [-79.764, 40.499, -71.857, 45.011],
    "north carolina":       [-84.325, 33.847, -75.451, 36.61],
    "north dakota":         [-104.048, 45.941, -96.556, 48.993],
    "ohio":                 [-84.824, 38.405, -80.52, 42.324],
    "oklahoma":             [-103.001, 33.647, -94.439, 37.0],
    "oregon":               [-124.567, 41.999, -116.454, 46.256],
    "pennsylvania":         [-80.521, 39.721, -74.698, 42.538],
    "rhode island":         [-71.878, 41.145, -71.121, 42.017],
    "south carolina":       [-83.356, 32.032, -78.573, 35.219],
    "south dakota":         [-104.057, 42.518, -96.453, 45.943],
    "tennessee":            [-90.313, 34.983, -81.658, 36.69],
    "texas":                [-106.663, 25.843, -93.514, 36.501],
    "utah":                 [-114.043, 36.999, -109.046, 42.001],
    "vermont":              [-73.439, 42.729, -71.505, 45.014],
    "virginia":             [-83.668, 36.539, -75.243, 39.451],
    "washington":           [-124.735, 45.566, -116.878, 48.993],
    "west virginia":        [-82.638, 37.204, -77.734, 40.647],
    "wisconsin":            [-92.902, 42.491, -86.25, 47.309],
    "wyoming":              [-111.052, 40.999, -104.052, 45.001],
}

# ---- Place-string normalisation --------------------------------------------
# retrieval.normalise_place() strips these before the lookup. "State of X" and
# "Commonwealth of X" are the formal styles of some states (Pennsylvania,
# Virginia, Massachusetts, Kentucky); " state" covers "Washington State". No
# key of BBOXES starts or ends with any of them (tests/test_us_places.py checks
# that every key normalises to itself).
PLACE_PREFIXES = ("state of ", "commonwealth of ")
PLACE_SUFFIXES = (" state", " united states", " usa")

# Tail words that turn a named feature into a descriptive phrase. Identical to
# au's and uk's: English common nouns. NOT READ by the evaluated configuration.
FEATURE_TAILS = ("catchment", "catchments", "system", "corridor", "reserve")

# Trailing words naming the KIND of local-government area rather than the area
# itself.
ADMIN_TAILS = ("county", "city", "town", "village", "township", "borough", "parish")

# ---- Gazetteer: none -------------------------------------------------------
# GAZETTEER_FILE None means expansion._load_gazetteer() returns empty tables
# without touching the disk, and retrieval.place_resolution_available() is
# already true from BBOXES alone.
GAZETTEER_FILE = None
