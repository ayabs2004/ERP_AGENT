IF NOT EXISTS (SELECT 1 FROM sys.messages WHERE message_id = 82086 AND language_id = 1036) -- 1036 = français
BEGIN
    IF NOT EXISTS (SELECT 1 FROM sys.messages WHERE message_id = 82086 AND language_id = 1033) -- 1033 = us_english
    BEGIN
        EXEC sp_addmessage
            @msgnum = 82086,
            @severity = 11,
            @msgtext = N'Document validation error (stock or BOM).',
            @lang = 'us_english';
    END

    EXEC sp_addmessage
        @msgnum = 82086,
        @severity = 11,
        @msgtext = N'Erreur de validation du document (stock ou nomenclature).',
        @lang = 'French';
END

IF NOT EXISTS (SELECT * FROM sys.objects WHERE object_id = OBJECT_ID(N'[dbo].[ERP_OPERATION]') AND type in (N'U'))
BEGIN
    CREATE TABLE ERP_OPERATION (
        operation_id VARCHAR(36) PRIMARY KEY,
        tool_name VARCHAR(100) NOT NULL,
        status VARCHAR(20) NOT NULL,
        request_payload NVARCHAR(MAX),
        response_payload NVARCHAR(MAX),
        created_at DATETIME DEFAULT GETDATE(),
        updated_at DATETIME DEFAULT GETDATE()
    );
END
