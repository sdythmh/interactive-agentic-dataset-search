"""Place resolution for data.gov.uk: hand-checked boxes, no gazetteer."""

# ---- Generated boxes -------------------------------------------------------
# [min_lon, min_lat, max_lon, max_lat], looked up by normalised place string.
# Generated from Natural Earth (see above); do not edit by hand.
BBOXES = {
    "united kingdom":           [-13.692, 49.909, 1.772, 60.848],
    "uk":                       [-13.692, 49.909, 1.772, 60.848],
    "great britain":            [-13.692, 49.909, 1.772, 60.848],
    "gb":                       [-13.692, 49.909, 1.772, 60.848],
    "england":                  [-6.349, 49.909, 1.772, 55.806],
    "scotland":                 [-13.692, 54.632, -0.754, 60.848],
    "wales":                    [-5.311, 51.384, -2.647, 53.427],
    "northern ireland":         [-8.174, 54.034, -5.43, 55.312],
    "ni":                       [-8.174, 54.034, -5.43, 55.312],
    "london":                   [-0.498, 51.282, 0.337, 51.69],
    "edinburgh":                [-3.452, 55.811, -3.077, 55.997],
    "cardiff":                  [-3.292, 51.453, -3.065, 51.554],
    "belfast":                  [-6.066, 54.54, -5.807, 54.664],
    "north east":               [-2.686, 54.441, -0.778, 55.806],
    "north west":               [-3.635, 52.947, -1.897, 55.191],
    "yorkshire and the humber": [-2.552, 53.289, 0.15, 54.558],
    "east midlands":            [-2.019, 51.976, 0.357, 53.608],
    "west midlands":            [-3.216, 51.817, -1.165, 53.214],
    "east of england":          [-0.731, 51.446, 1.772, 52.987],
    "south east":               [-1.951, 50.582, 1.449, 52.193],
    "south west":               [-6.349, 49.909, -1.485, 52.106],
}

# ---- Place-string normalisation --------------------------------------------
# retrieval.normalise_place() strips these before the lookup, so the router
# writing "Greater London" or "London Borough of Camden" still reaches a key.
# UK local-government names carry their tier in front of the place, which is
# the opposite of the Australian habit of putting it behind ("Mitchell Shire").
PLACE_PREFIXES = ("greater ", "city of ", "royal borough of ",
                  "london borough of ", "borough of ", "county of ")

# Stripped from the end. " england" is deliberately NOT here: it would turn
# "east of england" -- a key in the table above -- into "east of", which
# resolves to nothing.
PLACE_SUFFIXES = (" council", " region", " area", " uk", " united kingdom")

# Tail words that turn a named feature into a descriptive phrase.
FEATURE_TAILS = ("catchment", "catchments", "system", "corridor", "reserve")

# Trailing words naming the KIND of UK local-government area rather than the
# area itself.
ADMIN_TAILS = ("borough", "district", "county", "council", "city",
               "metropolitan", "unitary")

# ---- Gazetteer: none -------------------------------------------------------
# No gazetteer. None here means expansion._load_gazetteer() returns empty
# tables without touching the disk, and
# retrieval.place_resolution_available() is already true from BBOXES alone,
# so no path ever opens a file.
GAZETTEER_FILE = None
