from sqlalchemy import text
from database import engine


try:
    with engine.connect() as connection:

        # Check database connection details
        print("\n========== DATABASE CONNECTION ==========")

        result = connection.execute(
            text("""
                SELECT
                    current_database(),
                    current_schema(),
                    inet_server_addr(),
                    inet_server_port()
            """)
        )

        database_info = result.fetchone()

        print("Database:", database_info[0])
        print("Current Schema:", database_info[1])
        print("Server IP:", database_info[2])
        print("Port:", database_info[3])

        # List all application tables
        print("\n========== AVAILABLE TABLES ==========")

        tables = connection.execute(
            text("""
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_type = 'BASE TABLE'
                  AND table_schema NOT IN (
                      'pg_catalog',
                      'information_schema'
                  )
                ORDER BY table_schema, table_name
            """)
        ).fetchall()

        if tables:
            for table in tables:
                print(f"Schema: {table[0]} | Table: {table[1]}")
        else:
            print("No application tables found.")

        # Find the customers table
        print("\n========== CUSTOMER TABLE CHECK ==========")

        customer_tables = connection.execute(
            text("""
                SELECT table_schema, table_name
                FROM information_schema.tables
                WHERE table_type = 'BASE TABLE'
                  AND LOWER(table_name) = 'customers'
                ORDER BY table_schema
            """)
        ).fetchall()

        if not customer_tables:
            print("Customers table not found in this database.")
            print("Check the database and schema in pgAdmin.")

        else:
            for customer_table in customer_tables:
                schema_name = customer_table[0]

                print(
                    f"\nFound customers table in schema: {schema_name}"
                )

                # Check password formats without printing passwords
                query = text("""
                    SELECT
                        customer_id,
                        email,
                        LENGTH(password) AS password_length,
                        CASE
                            WHEN password LIKE '$2a$%'
                              OR password LIKE '$2b$%'
                              OR password LIKE '$2y$%'
                                THEN 'bcrypt hash'
                            WHEN password LIKE '$argon2%'
                                THEN 'argon2 hash'
                            WHEN password LIKE
                                'pbkdf2_sha256$%'
                                THEN 'PBKDF2 hash'
                            ELSE 'other format'
                        END AS password_format
                    FROM customers
                    ORDER BY customer_id
                """).columns()

                # Safely quote the discovered schema name
                from sqlalchemy.sql import quoted_name

                safe_schema = connection.dialect.identifier_preparer.quote(
                    schema_name
                )

                customer_query = text(f"""
                    SELECT
                        customer_id,
                        email,
                        LENGTH(password) AS password_length,
                        CASE
                            WHEN password LIKE '$2a$%'
                              OR password LIKE '$2b$%'
                              OR password LIKE '$2y$%'
                                THEN 'bcrypt hash'
                            WHEN password LIKE '$argon2%'
                                THEN 'argon2 hash'
                            WHEN password LIKE 'pbkdf2_sha256$%'
                                THEN 'PBKDF2 hash'
                            ELSE 'other format'
                        END AS password_format
                    FROM {safe_schema}.customers
                    ORDER BY customer_id
                """)

                customers = connection.execute(
                    customer_query
                ).fetchall()

                if customers:
                    for customer in customers:
                        print(customer)
                else:
                    print("Customers table exists but has no records.")

                break

        print("\n========== TEST COMPLETED ==========")


except Exception as error:
    print("\nDatabase test failed:")
    print(error)

