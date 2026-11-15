"""Каталог популярных в России автомобилей и параметров детейлинг-плёнки."""

# Популярные модели по маркам (российский рынок, включая новые китайские бренды).
# Список сгруппирован по марке; в интерфейсе выводится марка + модель.
CAR_BRANDS = {
    "LADA": [
        "Granta", "Vesta", "Vesta SW Cross", "Niva Legend", "Niva Travel",
        "Largus", "XRAY", "Kalina", "Priora", "2110", "2114", "2115",
        "4x4", "Iskra", "Aura",
    ],
    "Toyota": [
        "Camry", "Corolla", "RAV4", "Land Cruiser 200", "Land Cruiser 300",
        "Land Cruiser Prado", "Highlander", "C-HR", "Fortuner", "Hilux",
        "Prius", "Avensis", "Yaris", "Supra", "Crown", "Alphard", "Venza",
        "Sequoia", "Tundra", "4Runner",
    ],
    "Kia": [
        "Rio", "Cerato", "Sportage", "Sorento", "Soul", "Optima", "K5",
        "Ceed", "Seltos", "K900", "Picanto", "Stinger", "Carnival", "Mohave",
        "XCeed", "EV6", "Telluride", "Niro", "ProCeed",
    ],
    "Hyundai": [
        "Solaris", "Creta", "Tucson", "Santa Fe", "Elantra", "Accent",
        "Sonata", "i30", "i40", "ix35", "ix55", "Getz", "Staria", "Palisade",
        "Kona", "Bayon", "Veloster", "IONIQ 5", "IONIQ 6",
    ],
    "Renault": [
        "Logan", "Sandero", "Duster", "Kaptur", "Arkana", "Megane", "Scenic",
        "Laguna", "Fluence", "Symbol", "Clio", "Kangoo", "Trafic", "Master",
        "Koleos",
    ],
    "Volkswagen": [
        "Polo", "Golf", "Passat", "Tiguan", "Touareg", "Jetta", "Arteon",
        "Teramont", "Caddy", "Transporter", "Multivan", "Caravelle",
        "Scirocco", "Touran", "Sharan", "Amarok", "ID.4", "ID.6",
    ],
    "Skoda": [
        "Octavia", "Rapid", "Fabia", "Superb", "Kodiaq", "Karoq", "Yeti",
        "Roomster", "Scala", "Kamiq", "Enyaq",
    ],
    "Nissan": [
        "Qashqai", "X-Trail", "Almera", "Juke", "Murano", "Pathfinder",
        "Patrol", "Teana", "Note", "Tiida", "Micra", "Terrano", "Leaf",
        "Ariya", "Navara",
    ],
    "Ford": [
        "Focus", "Mondeo", "Kuga", "Explorer", "Fiesta", "EcoSport",
        "Transit", "Transit Custom", "Ranger", "Mustang", "Edge", "S-Max",
        "Galaxy", "Tourneo", "Bronco",
    ],
    "Chevrolet": [
        "Cruze", "Aveo", "Lacetti", "Captiva", "Niva", "Orlando", "Spark",
        "Malibu", "Camaro", "Tahoe", "Traverse", "Equinox", "Cobalt",
        "Blazer", "Suburban",
    ],
    "Mitsubishi": [
        "Lancer", "Outlander", "ASX", "Pajero", "Pajero Sport",
        "Eclipse Cross", "L200", "Galant", "Colt", "Attrage", "Space Star",
    ],
    "Mazda": [
        "3", "6", "CX-5", "CX-7", "CX-9", "CX-30", "CX-60", "MX-5",
        "Premacy", "MPV",
    ],
    "BMW": [
        "1 Series", "2 Series", "3 Series", "4 Series", "5 Series", "6 Series",
        "7 Series", "8 Series", "X1", "X2", "X3", "X4", "X5", "X6", "X7",
        "XM", "M3", "M5", "M8", "i3", "i4", "iX", "iX3", "Z4",
    ],
    "Mercedes-Benz": [
        "A-Class", "B-Class", "C-Class", "E-Class", "S-Class", "CLA", "CLS",
        "GLA", "GLB", "GLC", "GLE", "GLS", "G-Class", "V-Class", "Sprinter",
        "EQC", "EQE", "EQS", "ML", "GLK",
    ],
    "Audi": [
        "A1", "A3", "A4", "A5", "A6", "A7", "A8", "Q2", "Q3", "Q5", "Q7",
        "Q8", "TT", "R8", "e-tron", "Q4 e-tron", "RS6", "RS7",
    ],
    "Lexus": [
        "RX", "NX", "GX", "LX", "ES", "IS", "LS", "UX", "CT", "GS", "RC",
        "LC",
    ],
    "Volvo": [
        "XC40", "XC60", "XC90", "S40", "S60", "S80", "S90", "V40", "V60",
        "V90", "C30", "C40",
    ],
    "Land Rover": [
        "Range Rover", "Range Rover Sport", "Range Rover Evoque",
        "Range Rover Velar", "Discovery", "Discovery Sport", "Defender",
        "Freelander",
    ],
    "Porsche": [
        "911", "Cayenne", "Macan", "Panamera", "Taycan", "Boxster", "Cayman",
        "718",
    ],
    "Subaru": [
        "Forester", "Outback", "XV", "Impreza", "Legacy", "Levorg", "WRX",
        "BRZ", "Tribeca",
    ],
    "Honda": [
        "Civic", "Accord", "CR-V", "Pilot", "Fit", "Jazz", "HR-V", "Element",
        "Legend", "Insight",
    ],
    "Suzuki": [
        "Vitara", "Grand Vitara", "SX4", "Jimny", "Swift", "Ignis", "S-Cross",
        "Kizashi",
    ],
    "Peugeot": [
        "206", "207", "208", "301", "308", "408", "508", "2008", "3008",
        "5008", "Partner", "Expert", "Boxer", "4007", "4008",
    ],
    "Citroen": [
        "C3", "C4", "C5", "C3 Aircross", "C4 Picasso", "C5 Aircross",
        "Berlingo", "Jumpy", "Jumper", "DS3", "DS4", "DS5", "C-Elysee",
        "C6",
    ],
    "Opel": [
        "Astra", "Corsa", "Insignia", "Mokka", "Zafira", "Vectra", "Meriva",
        "Antara", "Vivaro", "Combo",
    ],
    "Chery": [
        "Tiggo 3", "Tiggo 4", "Tiggo 7 Pro", "Tiggo 8", "Tiggo 8 Pro",
        "Tiggo 9", "Arrizo 5", "Arrizo 8", "Explore 06", "Tiggo 7 Pro Max",
        "Tiggo 4 Pro", "Arrizo 6",
    ],
    "Haval": [
        "Jolion", "F7", "F7x", "Dargo", "H9", "H5", "Dargo X", "M6",
    ],
    "Geely": [
        "Coolray", "Atlas", "Atlas Pro", "Tugella", "Monjaro", "Emgrand",
        "Preface", "Okavango", "Cityray", "Starray",
    ],
    "Exeed": ["TXL", "VX", "LX", "RX", "Stellar", "Exlantix"],
    "Changan": [
        "CS35 Plus", "CS55 Plus", "CS75 Plus", "UNI-K", "UNI-T", "UNI-V",
        "Alsvin", "Hunter", "Lamore", "X7",
    ],
    "Omoda": ["C5", "S5", "E5"],
    "Jaecoo": ["J7", "J8"],
    "Tank": ["300", "500", "700", "800"],
    "Great Wall": ["Hover", "Safe", "Poer", "Wingle"],
    "FAW": ["Besturn B50", "Besturn X40", "Besturn V5", "Besturn V80", "T77", "T99"],
    "Lifan": ["Solano", "X50", "X60", "X70", "Smily", "Cebrium", "Murman"],
    "Ravon": ["R2", "R4", "Nexia", "Gentra", "Spark"],
    "UAZ": ["Patriot", "Hunter", "Profi", "Bukhanka", "Pickup"],
    "GAZ": ["Gazelle", "Gazelle Next", "Sobol", "Valdai", "Next"],
    "Moskvich": ["3", "3e", "6", "8"],
    "Tesla": ["Model 3", "Model Y", "Model S", "Model X"],
    "Genesis": ["G70", "G80", "G90", "GV70", "GV80", "GV60"],
    "Infiniti": ["QX50", "QX60", "QX80", "Q50", "QX70", "Q30", "Q60"],
    "Acura": ["MDX", "RDX", "TLX"],
    "Cadillac": ["Escalade", "XT5", "XT6", "CT5", "CT6"],
    "Jeep": ["Grand Cherokee", "Wrangler", "Cherokee", "Compass", "Renegade"],
    "Dodge": ["Ram", "Challenger", "Charger", "Durango"],
    "Chrysler": ["300C", "PT Cruiser"],
    "Jaguar": ["XF", "XJ", "F-Pace", "E-Pace", "I-Pace", "F-Type"],
    "Mini": ["Cooper", "Countryman", "Clubman"],
    "Smart": ["Fortwo", "Forfour"],
    "Dacia": ["Logan", "Sandero", "Duster"],
    "Fiat": ["Ducato", "Doblo", "500", "500X"],
    "SsangYong": ["Actyon", "Kyron", "Rexton", "Tivoli"],
    "BYD": ["Song Plus", "Han", "Tang", "Qin Plus", "Seal", "Dolphin", "Atto 3"],
    "Zeekr": ["001", "007", "X", "009"],
    "Li Auto": ["L6", "L7", "L9"],
    "Nio": ["ES6", "ET5", "ES8"],
    "Xpeng": ["P7", "G6", "G9"],
    "Avatr": ["11", "12"],
    "Deepal": ["S7", "SL03"],
    "AITO": ["M5", "M7", "M9"],
    "Hongqi": ["H5", "HS5", "H9", "E-HS9", "HQ9"],
    "Voyah": ["Free", "Dreamer", "Passion"],
    "Dongfeng": ["Aeolus AX7", "Rich 6", "Huge", "Friday"],
    "JAC": ["S3", "S5", "T6", "JS4"],
    "BAIC": ["X35", "X55", "X7", "BJ40"],
    "Kaiyi": ["E5", "X3", "X7"],
    "SWM": ["G01", "G01F", "X7"],
    "Brilliance": ["V3", "V5", "H230"],
    "Zotye": ["T600", "Coupa", "Z300"],
    "Daewoo": ["Nexia", "Matiz", "Espero", "Leganza", "Lanos"],
    "IZH": ["2125", "2126", "2715", "412"],
    "Isuzu": ["D-Max", "MU-X"],
}

# Плоский список для выпадающего списка и API.
CAR_MODELS = [
    {"brand": brand, "model": model}
    for brand, models in CAR_BRANDS.items()
    for model in models
]

CAR_COLORS = [
    "чёрный", "белый", "серебристый", "серый", "тёмно-серый", "синий",
    "голубой", "красный", "бордовый", "зелёный", "бежевый", "коричневый",
    "жёлтый", "оранжевый", "фиолетовый",
]

BODY_COLORS = ["как на фото"] + CAR_COLORS

FILM_FINISHES = [
    {"id": "matte", "label": "Матовая"},
    {"id": "gloss", "label": "Глянцевая"},
]

FILM_COLORS = ["в цвет кузова"] + CAR_COLORS

ANTI_CHROME_OPTIONS = [
    {"id": "none", "label": "Без антихрома"},
    {"id": "matte_black", "label": "Антихром: матовый чёрный"},
    {"id": "gloss_black", "label": "Антихром: глянцевый чёрный"},
]

ASPECT_RATIOS = ["16:9", "4:3", "1:1", "3:2"]
