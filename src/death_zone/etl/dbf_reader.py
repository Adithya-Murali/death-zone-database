from dbfread import DBF

table = DBF('people.dbf')
for record in table:
    print(record)
