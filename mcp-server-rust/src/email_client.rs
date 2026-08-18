use async_imap::Session;
use tokio::net::TcpStream;
use tokio_native_tls::{TlsConnector, TlsStream};
use tokio_util::compat::{Compat, TokioAsyncReadCompatExt};
use std::env;

pub type ImapSession = Session<Compat<TlsStream<TcpStream>>>;

/// Establishes a secure TLS connection to the IMAP server and logs in.
pub async fn connect() -> Result<ImapSession, String> {
    let domain = env::var("IMAP_SERVER").unwrap_or_else(|_| "imap.gmail.com".to_string());
    let username = env::var("IMAP_USER").map_err(|_| "Missing IMAP_USER environment variable")?;
    let password = env::var("IMAP_PASS").map_err(|_| "Missing IMAP_PASS environment variable")?;

    eprintln!("Connecting to IMAP server at {}...", domain);

    let native_tls = native_tls::TlsConnector::builder()
        .build()
        .map_err(|e| format!("Failed to build TLS connector: {}", e))?;
    let tls = TlsConnector::from(native_tls);

    let tcp_stream = TcpStream::connect((domain.as_str(), 993))
        .await
        .map_err(|e| format!("Could not connect to TCP stream: {}", e))?;
    
    let tls_stream = tls.connect(domain.as_str(), tcp_stream)
        .await
        .map_err(|e| format!("TLS handshake failed: {}", e))?;

    // Wrap the Tokio TLS stream in a Compat layer so the futures-based IMAP client can read it
    let compat_stream = tls_stream.compat();
    let client = async_imap::Client::new(compat_stream);

    eprintln!("Authenticating user '{}'...", username);
    let session = client.login(username, password)
        .await
        .map_err(|(e, _)| format!("IMAP login failed: {}", e))?;

    eprintln!("IMAP Connection Successful!");
    Ok(session)
}
