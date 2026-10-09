// Where the API is and which AWS Region this copy is deployed in. scripts/build_site.py
// writes this file for a deployment (API_BASE is the stack's ApiUrl output). Left empty,
// the page talks to the server it came from, as the local dev server expects.
window.SURYA_CONFIG = { API_BASE: "", REGION: "", CROSS_REGION: false };
