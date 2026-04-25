from lawvm.eu.pipeline import EUReplayPipeline

def debug_gdpr() -> None:
    pipeline = EUReplayPipeline()
    celex = "32016R0679"

    print(f"--- Discovering affecting acts for {celex} ---")
    affecting = pipeline.discover_affecting_acts(celex)
    print(f"Affecting acts: {affecting}")

    if affecting:
        target_act = affecting[0]
        print(f"--- Fetching amendment text for {target_act} ---")
        text = pipeline.fetch_amendment_text(target_act)
        print(f"Text length: {len(text)}")
        if text:
            print("Preview:")
            print(text[:500])
    else:
        print("No affecting acts discovered.")

if __name__ == "__main__":
    debug_gdpr()
