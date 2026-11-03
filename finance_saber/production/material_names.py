"""The materials' official names (the list «Сырье наименование», November 2026) and the old names they replace.

Used by migration 0009_official_material_names, which renames the materials in place
(their stock, deliveries, production history and recipe norms stay with them), and
by the workbook importer, whose sheets still use the old names.
"""

RAW, PACKAGING = "raw", "packaging"

# (name, unit, kind), in the list's order.
OFFICIAL = [
    ("Сахар-песок белый кристаллический", "кг", RAW),
    ("Citric Acid Monohydrate/ Лимонная кислота моногидрат (F) Е330", "кг", RAW),
    ("TRSODIUM CITRATE/Цитрат натрия (F) Е331", "кг", RAW),
    ("Аскорбиновая кислота Е300", "кг", RAW),
    ("Potassium sorbate/Сорбат калия (F) Е202", "кг", RAW),
    ("Бензоат натрий /Sodium Benzoate Powder (F) Е211", "кг", RAW),
    ('Концентрат для напитков "Энергия"', "кг", RAW),
    ('Ароматизатор для пищевых продуктов со вкусом "Тутти Фрутти"', "кг", RAW),
    ("Сок яблочный концентрированный 70 %", "кг", RAW),
    ("Ароматизатор Strawberry (клубника) Flavour 77880-33", "кг", RAW),
    ("Ароматизатор Lemon (лимон) Flavour L-256916", "кг", RAW),
    ("Ароматизатор Мята 2.14498.835", "кг", RAW),
    ("Ароматизатор Pomegranate (гранат) Flavour L-103130", "кг", RAW),
    ("Ароматизатор Mango (Манго) flavour KG-318-354-8", "кг", RAW),
    ("Ароматизатор пищевой Tropical Fruit Blend Flavour Emulsion RW-440-328-8", "кг", RAW),
    ("Ароматизатор Apple (яблоко) Flavour L-146321", "кг", RAW),
    ("Ароматизатор Orange (Апельсин) Flavour СG-056-993-8", "кг", RAW),
    ("Ароматизатор Neutral Type Cloud E-170818", "кг", RAW),
    ("caramel color E 150/d Карамельный колер E150d", "кг", RAW),
    ("Пищевой краситель ИДАКОЛ Красный очаровательный АС / Food color IDACOL Allura Red AC (F) Е129", "кг", RAW),
    ("Пищевой краситель Идакол Тартразин / Food color IDACOL TARTRAZINE Е102 (F)", "кг", RAW),
    ("Пищевой краситель Идакол Желтый солнечный закат / Food color IDACOL Sunset yellow E110 (F)", "кг", RAW),
    ("Пищевой краситель синтетический Марки IDACOL Кармуазин E122(F)", "кг", RAW),
    ("Greentea base (зеленый чай основа) 2.07162.352", "кг", RAW),
    ("Natural Flavouring Green Tea (Ар. натуральный зеленый чай) 5.83321.673", "кг", RAW),
    ("Natural Flavouring Honey (Ар. Мёд) 5.14473.365", "кг", RAW),
    ("Natural Camomile Flavouring (Ар. Ромашка) 5.81176.042", "кг", RAW),
    ("Natural Flavouring Mango (Ар. Манго) 5.82541.560", "кг", RAW),
    ("Raspberry Flavouring (Ар. Малина) 5.83626.365", "кг", RAW),
    ("Greentea Flavouring (Ар. зеленый чай) 5.14947.890", "кг", RAW),
    ("Black Turkish Tea Extract (экстракт чёрного чая) 9.08760.016", "кг", RAW),
    ("Natural Flavouring Apple (Ар.натуралный Яблока) 5.12308.365", "кг", RAW),
    ("Natural Tea Flavouring with other Natural Flavourings (Ар.натуралный чай +ароматизатор ) 5.81179.890", "кг", RAW),
    ("Natural Flavouring (Cinnamon-Clove) 5.87901.365", "кг", RAW),
    ("Natural Orange Flavouring (Ар.натуралный апельсин ) 2.00540.042", "кг", RAW),
    ("Natural Flavouring Cinnamon (Ар.натуралный корица) 5.11815.390", "кг", RAW),
    ("Natural Flavouring Lemon (Ар.натуралный лимон) 2.07717.397", "кг", RAW),
    ("Natural Ginger Flavouring (Ар.натуралный имбир) 5.11782.042", "кг", RAW),
    ("Natural Chilli Flavouring (Ар.натуралный чили) 8.14046.890", "кг", RAW),
    ("Honey Flavouring (Ар. Мёд) 2.01061.890", "кг", RAW),
    ("Банки алюминевые A Sakura green tea honey (мёд) 0,45л", "шт", PACKAGING),
    ("Банки алюминевые A Sakura green tea mango (манго-ромашка) 0,45л", "шт", PACKAGING),
    ("Банки алюминевые A Sakura green tea raspberry (малина) 0,45л", "шт", PACKAGING),
    ("Банки алюминевые A Sakura black tea apple (Яблока - корица-гвоздика) 0,45л", "шт", PACKAGING),
    ("Банки алюминевые A Sakura black tea orange (Апельсин-корица) 0,45л", "шт", PACKAGING),
    ("Банки алюминевые A Sakura black tea honey (лимон-мёд-имбир) 0,45л", "шт", PACKAGING),
    ("Банки алюминевые Bfresh Garnet 0,45л", "шт", PACKAGING),
    ("Банки алюминевые Bfresh strawberry 0,45л", "шт", PACKAGING),
    ("Банки алюминевые Bfresh Lime 0,45л", "шт", PACKAGING),
    ("Банки алюминевые Bfresh Mango 0,45л", "шт", PACKAGING),
    ("Банки алюминевые A Bfresh Orange 0,45л", "шт", PACKAGING),
    ("Банки алюминевые A Bfresh Apple 0,45л", "шт", PACKAGING),
    ("Банки алюминевые Energy Drink 0,45л", "шт", PACKAGING),
    ("Банки алюминевые Energy Drink 0,25л", "шт", PACKAGING),
    ("Алюминиевая крышка тип 200 gold", "шт", PACKAGING),
    ("Алюминиевая крышка тип 202 gold", "шт", PACKAGING),
    ("Гофра поддон 3-сл. 25,5*19,2*5 см для 450 мл напиток (12 шт)", "шт", PACKAGING),
    ("гофра поддон 3-сл. 22*16.5*4 см для 250 мл напиток (12 шт)", "шт", PACKAGING),
    ("гофра поддон 3-сл. 25,5*19,2*5 см для 450 мл напиток (12 шт) C", "шт", PACKAGING),
    ("гофра поддон 3-сл. 38.4*25.7*4.5 см для 450 мл напиток (24 шт)", "шт", PACKAGING),
    ("Самоклеющаяся этикетка 15*15", "шт", PACKAGING),
    ("Агрегационный Штрих-код 60*30мм", "шт", PACKAGING),
]

# Old name -> official name. Several old names on one official name are merged into it
# (the flavoured Energy cans: all Energy drinks of 0,45 l use one can).
RENAMED = {
    "Сахар": "Сахар-песок белый кристаллический",
    "Лимоный кислота Е330": "Citric Acid Monohydrate/ Лимонная кислота моногидрат (F) Е330",
    "Цитрат натрия E331": "TRSODIUM CITRATE/Цитрат натрия (F) Е331",
    "Аскарбиновый кислота Е300": "Аскорбиновая кислота Е300",
    "Сорбат калий Е202": "Potassium sorbate/Сорбат калия (F) Е202",
    "Бензоат натрия E211": "Бензоат натрий /Sodium Benzoate Powder (F) Е211",
    'Концентрат для напитков "Энергия "': 'Концентрат для напитков "Энергия"',
    "Ар. Тутти-фрути": 'Ароматизатор для пищевых продуктов со вкусом "Тутти Фрутти"',
    "Яблочный сок 70%": "Сок яблочный концентрированный 70 %",
    "Ар клубника 77880-33": "Ароматизатор Strawberry (клубника) Flavour 77880-33",
    "Ар лимон L-256916": "Ароматизатор Lemon (лимон) Flavour L-256916",
    "ар.мията 2.14498.835(DOHLER)": "Ароматизатор Мята 2.14498.835",
    "Ар гранат L-103130": "Ароматизатор Pomegranate (гранат) Flavour L-103130",
    "Ар. Манго KG 318-353-8": "Ароматизатор Mango (Манго) flavour KG-318-354-8",
    "Tropikal Fruit Blend Flavour Emulsion RW-440-328-8": (
        "Ароматизатор пищевой Tropical Fruit Blend Flavour Emulsion RW-440-328-8"
    ),
    "ар. Олма L -146321": "Ароматизатор Apple (яблоко) Flavour L-146321",
    "Ар . Апельсин CG 056-933-8": "Ароматизатор Orange (Апельсин) Flavour СG-056-993-8",
    "Нейтральный Эмулься E170818": "Ароматизатор Neutral Type Cloud E-170818",
    "Карамельный колер Е150d": "caramel color E 150/d Карамельный колер E150d",
    'Краситель "красный очеровательный " Е129': (
        "Пищевой краситель ИДАКОЛ Красный очаровательный АС / Food color IDACOL Allura Red AC (F) Е129"
    ),
    'Краситель " тартразн" E102': "Пищевой краситель Идакол Тартразин / Food color IDACOL TARTRAZINE Е102 (F)",
    'Краситель "солночный закат" Е110': (
        "Пищевой краситель Идакол Желтый солнечный закат / Food color IDACOL Sunset yellow E110 (F)"
    ),
    'Краситель "Кармуазин" Е122': "Пищевой краситель синтетический Марки IDACOL Кармуазин E122(F)",
    "зелёний чай основа D07162": "Greentea base (зеленый чай основа) 2.07162.352",
    "Ар. натуралный зел чая К83321": "Natural Flavouring Green Tea (Ар. натуральный зеленый чай) 5.83321.673",
    "Ар. Мёд К14473": "Natural Flavouring Honey (Ар. Мёд) 5.14473.365",
    "Ар. Ромашка К81176": "Natural Camomile Flavouring (Ар. Ромашка) 5.81176.042",
    "Ар. Манго К82541": "Natural Flavouring Mango (Ар. Манго) 5.82541.560",
    "Ар. Малина К83626": "Raspberry Flavouring (Ар. Малина) 5.83626.365",
    "Ар.зелёный чая К14947": "Greentea Flavouring (Ар. зеленый чай) 5.14947.890",
    "экстракть чёрного чая Н08760": "Black Turkish Tea Extract (экстракт чёрного чая) 9.08760.016",
    "Ар.натуралный Яблока К 12308": "Natural Flavouring Apple (Ар.натуралный Яблока) 5.12308.365",
    "Ар.натуралный чая +ароматизатор К81179": (
        "Natural Tea Flavouring with other Natural Flavourings (Ар.натуралный чай +ароматизатор ) 5.81179.890"
    ),
    "Натуральный ароматизатор(корица-гвоздика) К87901": "Natural Flavouring (Cinnamon-Clove) 5.87901.365",
    "Ар.натуралный апельсин D00540": "Natural Orange Flavouring (Ар.натуралный апельсин ) 2.00540.042",
    "Ар.натуралный (корица) К11815": "Natural Flavouring Cinnamon (Ар.натуралный корица) 5.11815.390",
    "Ар.натуралный лимон D07177": "Natural Flavouring Lemon (Ар.натуралный лимон) 2.07717.397",
    "Ар.натуралный имбир К11782": "Natural Ginger Flavouring (Ар.натуралный имбир) 5.11782.042",
    "натуралный ароматизатор чили В 14046": "Natural Chilli Flavouring (Ар.натуралный чили) 8.14046.890",
    "Ар. Мёд D01061": "Honey Flavouring (Ар. Мёд) 2.01061.890",
    "банка Green Tea мёд 0,450 л": "Банки алюминевые A Sakura green tea honey (мёд) 0,45л",
    "банка Green Tea манго - ромашка0,450 л": "Банки алюминевые A Sakura green tea mango (манго-ромашка) 0,45л",
    "банка Green Tea малина 0,450 л": "Банки алюминевые A Sakura green tea raspberry (малина) 0,45л",
    "банка Blak Tea Яблока - корица-гвоздика 0,450 л": (
        "Банки алюминевые A Sakura black tea apple (Яблока - корица-гвоздика) 0,45л"
    ),
    "банка Blak Tea Апельсин-корица 0,450 л": "Банки алюминевые A Sakura black tea orange (Апельсин-корица) 0,45л",
    "банка Blak Tea лимон-мёд-имбир 0,450 л": "Банки алюминевые A Sakura black tea honey (лимон-мёд-имбир) 0,45л",
    "B Fresh анор банка 0,450 л": "Банки алюминевые Bfresh Garnet 0,45л",
    "B Fresh клубника банка 0,450 л": "Банки алюминевые Bfresh strawberry 0,45л",
    "B Fresh лайм банка 0,450 л": "Банки алюминевые Bfresh Lime 0,45л",
    "B Fresh Манго банка 0,450 л": "Банки алюминевые Bfresh Mango 0,45л",
    "B Fresh апельсин банка 0,450 л": "Банки алюминевые A Bfresh Orange 0,45л",
    "B Fresh олма банка 0,450 л": "Банки алюминевые A Bfresh Apple 0,45л",
    "7 Energy Drink банка 0,450 л": "Банки алюминевые Energy Drink 0,45л",
    "7 Energy Drink mango банка 0,450 л": "Банки алюминевые Energy Drink 0,45л",
    "7 Energy Drink апелсин банка 0,450 л": "Банки алюминевые Energy Drink 0,45л",
    "7 Energy Pomegranat банка0,450 л": "Банки алюминевые Energy Drink 0,45л",
    "7 Energy Drink банка 0,250 л": "Банки алюминевые Energy Drink 0,25л",
    "Золотой кришка 200": "Алюминиевая крышка тип 200 gold",
    "Золотой кришка 202": "Алюминиевая крышка тип 202 gold",
    "Гофра паддон saber 450 мл\\12 шт": "Гофра поддон 3-сл. 25,5*19,2*5 см для 450 мл напиток (12 шт)",
    "Гофра паддон saber 250 мл\\12 шт": "гофра поддон 3-сл. 22*16.5*4 см для 250 мл напиток (12 шт)",
    "Гофра паддон sakura 450 мл\\12 шт": "гофра поддон 3-сл. 25,5*19,2*5 см для 450 мл напиток (12 шт) C",
    "Гофра паддон saber 450 мл\\24 шт": "гофра поддон 3-сл. 38.4*25.7*4.5 см для 450 мл напиток (24 шт)",
    "агрегационный код": "Агрегационный Штрих-код 60*30мм",
}

# Not on the list: taken out of the recipes and no longer used (kept, inactive, where
# production history refers to them).
RETIRED = ["клей", "плёнка", "стрич", "Основа барбарис"]


def official_name(name: str) -> str | None:
    """The official name for a material's old (or official) name; None for a retired material."""
    if name in RETIRED:
        return None
    return RENAMED.get(name, name)
