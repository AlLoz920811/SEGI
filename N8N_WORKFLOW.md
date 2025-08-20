# N8N Workflow Documentation

This document outlines the N8N workflow for processing PDF documents through various stages from email receipt to database insertion.

## Overview

The workflow consists of five main processes that work together to process incoming PDF documents:

1. **Upload Files from Email**
2. **Split Files**
3. **Extract Files**
4. **Generate Files**
5. **Insert Files**

## 1. Upload Files from Email (Subir archivos desde e-mail)

**Trigger**: New email with attachment (IMAP)

**Process Flow**:
1. **Email Trigger (IMAP)**: Monitors for new emails with attachments
2. **Code Node**: Processes initial email information
3. **Conditional Check (If)**:
   - Verifies if the attachment is a PDF
   - If true: Proceeds to next step
   - If false: Ends the workflow for this email
4. **Code1 Node**: Performs additional processing
5. **FTP Node**: Uploads the file to the FTP server

## 2. Split Files (Dividir Archivos)

**Trigger**: Scheduled (e.g., every hour)

**Process Flow**:
1. **Schedule Trigger**: Activates the workflow at set intervals
2. **FTP1 Node**: Lists available files on the FTP server
3. **Conditional Check (If1)**:
   - Verifies if there are new files to split
   - If true: Proceeds to next step
   - If false: Ends the workflow
4. **HTTP Request (GET /split)**: Calls the split endpoint to divide the PDF into individual pages

## 3. Extract Files (Extraer Archivos)

**Trigger**: Scheduled

**Process Flow**:
1. **Schedule Trigger2**: Activates the workflow at set intervals
2. **FTP2 Node**: Lists files in the processing directory
3. **Conditional Check (If2)**:
   - Verifies if there are split files ready for extraction
   - If true: Proceeds to next step
   - If false: Ends the workflow
4. **HTTP Request (GET /extract)**: Extracts content from the split PDF pages

## 4. Generate Files (Generar Archivos)

**Trigger**: Scheduled

**Process Flow**:
1. **Schedule Trigger1**: Activates the workflow at set intervals
2. **FTP3 Node**: Lists files in the extraction directory
3. **Conditional Check (If3)**:
   - Verifies if there are extracted files ready for processing
   - If true: Proceeds to next step
   - If false: Ends the workflow
4. **HTTP Request (GET /generate)**: Processes extracted data into structured format

## 5. Insert Files (Insertar Archivos)

**Trigger**: Scheduled

**Process Flow**:
1. **Schedule Trigger3**: Activates the workflow at set intervals
2. **FTP4 Node**: Lists files in the generation directory
3. **Conditional Check (If4)**:
   - Verifies if there are generated files ready for insertion
   - If true: Proceeds to next steps
   - If false: Ends the workflow
4. **HTTP Request (GET /insert)**: Inserts structured data into the database
5. **HTTP Request (GET /captura)**: Records the transaction or moves the processed file

## Error Handling

Each workflow includes error handling to:
- Log errors to a central location
- Retry failed operations
- Notify administrators of critical failures
- Move failed files to a quarantine area for manual review

## Monitoring

The workflow includes monitoring nodes that:
- Track processing times
- Log successful operations
- Monitor system resources
- Generate alerts for abnormal conditions

## Dependencies

- N8N instance with appropriate permissions
- FTP server access
- Database connection
- Email server access (IMAP)
- API endpoints for processing (/split, /extract, /generate, /insert, /captura)

## Security Considerations

- All credentials are stored securely in N8N's credential store
- File transfers use secure protocols (SFTP/FTPS)
- Sensitive data is encrypted in transit and at rest
- Access to the N8N interface is restricted to authorized personnel

![N8N Workflow](path/to/your/image.png)