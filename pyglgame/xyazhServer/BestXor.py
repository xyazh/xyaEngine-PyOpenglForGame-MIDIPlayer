import hmac
import base64
from .lzstring import LZString

class BestXor:
    lzstring = LZString()
    @staticmethod
    def encryptXor(key:bytes, plaintext:bytes)->bytes:
        expanded_key = key * (len(plaintext) // len(key)) + key[:len(plaintext) % len(key)]
        ciphertext = bytes(a ^ b for a, b in zip(plaintext, expanded_key))
        return ciphertext

    @staticmethod
    def decryptXor(key:bytes, ciphertext:bytes)->bytes:
        expanded_key = key * (len(ciphertext) // len(key)) + key[:len(ciphertext) % len(key)]
        plaintext = bytes(a ^ b for a, b in zip(ciphertext, expanded_key))
        return plaintext

    @staticmethod
    def safeHash(key:str|bytes,data:str|bytes)->str:
        if isinstance(key,str):
            key = bytes(key,encoding="utf8")
        if isinstance(data,str):
            data = bytes(data,encoding="utf8")
        h = hmac.new(key, data, digestmod='SHA256')
        return h.hexdigest()
    
    @staticmethod
    def encodeBase64(data:bytes)->str:
        base64_str = base64.b64encode(data).decode('utf-8')
        return base64_str
    
    @staticmethod
    def decodeBase64(base64_str:str)->bytes:
        byte_str = base64.b64decode(base64_str)
        return byte_str
    
    @staticmethod
    def bestEncryptXor(key:bytes, plaintext:bytes)->str:
        key = BestXor.safeHash(key,key).encode("utf8")
        plaintext = BestXor.encryptXor(key,plaintext)
        plaintext:str = BestXor.encodeBase64(plaintext)
        plaintext = BestXor.lzstring.compressToBase64(plaintext)
        return plaintext

    @staticmethod
    def bestDecryptXor(key:bytes, ciphertext:str)->bytes:
        key = BestXor.safeHash(key,key).encode("utf8")
        ciphertext = BestXor.lzstring.decompressFromBase64(ciphertext)
        ciphertext = BestXor.decodeBase64(ciphertext)
        ciphertext = BestXor.decryptXor(key,ciphertext)
        return ciphertext

if __name__ == "__main__":
    s = b"114"
    s = BestXor.bestEncryptXor(b"114514",s)
    print(s)
    s = BestXor.bestDecryptXor(b"114514",s)
    print(s.decode("utf8"))