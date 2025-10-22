from flask import Flask, request, jsonify, render_template
from azure.storage.blob import BlobServiceClient, PublicAccess, ContentSettings
from datetime import datetime
import os
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from azure.identity import DefaultAzureCredential
# Note: For local testing, you might need to run 'pip install python-dotenv'
# and load environment variables if they are not system-wide.
# 
load_dotenv()

# --- Configuration ---
CONNECTION_STRING = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
STORAGE_ACCOUNT_URL = os.environ.get("STORAGE_ACCOUNT_URL")
CONTAINER_NAME = os.environ.get("IMAGES_CONTAINER", "lanternfly-images")
MAX_FILE_SIZE_MB = 10
MAX_FILE_SIZE = MAX_FILE_SIZE_MB * 1024 * 1024
ALLOWED_CONTENT_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"]

# Initialize BlobServiceClient
bsc = None
if CONNECTION_STRING:
    try:
        bsc = BlobServiceClient.from_connection_string(CONNECTION_STRING)
    except Exception as e:
        print(f"Error initializing BlobServiceClient from CONNECTION_STRING: {e}")
elif STORAGE_ACCOUNT_URL:
    try:
        # Use DefaultAzureCredential for Azure App Service (Managed Identity)
        credential = DefaultAzureCredential()
        bsc = BlobServiceClient(account_url=STORAGE_ACCOUNT_URL, credential=credential)
    except Exception as e:
        print(f"Error initializing BlobServiceClient from STORAGE_ACCOUNT_URL: {e}")

# Initialize ContainerClient and ensure public access
cc = None
if bsc:
    cc = bsc.get_container_client(CONTAINER_NAME)
    try:
        # Create container if it doesn't exist, and set public-read access
        # If the container already exists, BlobStorage will raise a 409 conflict, which we ignore.
        cc.create_container(public_access=PublicAccess.CONTAINER)
        print(f"Container '{CONTAINER_NAME}' ensured to be public-read.")
    except Exception as e:
        if "ContainerAlreadyExists" not in str(e):
            print(f"Error ensuring container: {e}")
else:
    print("Container client could not be initialized. Check environment variables.")


app = Flask(__name__)
# Set max content length for Flask for automatic size validation
app.config["MAX_CONTENT_LENGTH"] = MAX_FILE_SIZE

# --- Utility Functions ---


def validate_image(file):
    """Checks file content type."""
    if file.content_type not in ALLOWED_CONTENT_TYPES:
        return (
            False,
            f"Invalid content type: {file.content_type}. Only image types are allowed.",
        )
    return True, None


def create_blob_name(filename):
    """Creates a sanitized, timestamped blob name."""
    timestamp = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
    # Use secure_filename to sanitize the original filename
    sanitized_filename = secure_filename(filename)
    if not sanitized_filename:
        sanitized_filename = "upload"
    return f"{timestamp}-{sanitized_filename}"


# --- API Endpoints ---


@app.post("/api/v1/upload")
def upload():
    # 1. Check for file presence
    if "file" not in request.files:
        return jsonify(ok=False, error="No file part in the request"), 400

    f = request.files["file"]

    if f.filename == "":
        return jsonify(ok=False, error="No selected file"), 400

    # 2. Validate file type
    is_valid, error_msg = validate_image(f)
    if not is_valid:
        return jsonify(ok=False, error=error_msg), 415  # Unsupported Media Type

    if cc is None:
        return jsonify(ok=False, error="Azure Blob Storage not configured"), 500

    try:
        # 3. Create blob name with timestamp
        blob_name = create_blob_name(f.filename)
        blob_client = cc.get_blob_client(blob_name)

        # 4. Upload the file
        # Use ContentSettings to ensure proper Content-Type is set for public access
        f.seek(0)  # Ensure we read from the start of the stream
        blob_client.upload_blob(
            f,
            overwrite=True,
            content_settings=ContentSettings(content_type=f.content_type),
        )

        # 5. Return success response
        public_url = f"{cc.url}/{blob_name}"
        print(f"Successfully uploaded blob: {blob_name}")
        return jsonify(ok=True, url=public_url), 200

    except Exception as e:
        # Catch exceptions including size limit exceeded (MAX_CONTENT_LENGTH)
        # Flask converts size errors to RequestEntityTooLarge (413) before this block,
        # but catching general errors is good practice.
        error_message = str(e)
        if "Request body larger than max size" in error_message:
            error_message = f"File size exceeds the limit of {MAX_FILE_SIZE_MB}MB."
            return jsonify(ok=False, error=error_message), 413

        print(f"Upload failed: {error_message}")
        return jsonify(ok=False, error=error_message), 500


@app.get("/api/v1/gallery")
def gallery():
    if cc is None:
        return jsonify(ok=False, error="Azure Blob Storage not configured"), 500

    try:
        # List blobs in the public container
        blob_list = cc.list_blobs()
        gallery_urls = []
        for blob in blob_list:
            # Construct the public URL
            public_url = f"{cc.url}/{blob.name}"
            gallery_urls.append(public_url)

        print(f"Gallery fetched: {len(gallery_urls)} images.")
        return jsonify(ok=True, gallery=gallery_urls), 200

    except Exception as e:
        print(f"Gallery fetch failed: {e}")
        return jsonify(ok=False, error=str(e)), 500


@app.get("/api/v1/health")
def health():
    """Health check endpoint. Checks connection to Azure Blob Storage."""
    if cc is None:
        return jsonify(
            status="UNHEALTHY", message="Storage client failed to initialize"
        ), 503
    try:
        # Simple check: try to get container properties (cheap operation)
        cc.get_container_properties()
        return jsonify(status="OK", message="Azure Storage connection successful"), 200
    except Exception as e:
        return jsonify(
            status="DEGRADED", message=f"Storage connection failed: {e}"
        ), 503


@app.get("/")
def index():
    """Renders the main gallery/upload page."""
    return render_template("index.html")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=True)