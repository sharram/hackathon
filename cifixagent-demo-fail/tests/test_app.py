import numpy  # not in requirements.txt
from app import fetch

def test_fetch():
    assert fetch() == 200
    
