from google.cloud import firestore

def main():
    db = firestore.Client(project="project-52f0a1c9-3feb-46e6-bce")
    doc_ref = db.collection("spike_test").document("day0")
    doc_ref.set({"status": "SPIKE_OK", "source": "day0-firestore-roundtrip"})
    doc = doc_ref.get()
    print(f"Firestore round trip: {doc.to_dict()}")

if __name__ == "__main__":
    main()
