"""仅使用桌面 Secret Service，不接受明文文件密钥环后端。"""

SERVICE = "io.github.swuljq.CampusNetwork"


class SecretStore:
    def __init__(self):
        self.memory = {}

    @staticmethod
    def backend():
        from keyring.backends.SecretService import Keyring

        return Keyring()

    def get(self, credential_id):
        if credential_id in self.memory:
            return self.memory[credential_id]
        try:
            value = self.backend().get_password(SERVICE, credential_id) or ""
        except Exception:
            raise RuntimeError("无法读取系统密钥环；请解锁密钥环或重新输入本次使用的密码") from None
        self.memory[credential_id] = value
        return value

    def set(self, credential_id, password, remember=True):
        if remember:
            try:
                self.backend().set_password(SERVICE, credential_id, password)
            except Exception:
                raise RuntimeError("密码未保存：系统密钥环不可用，可取消“保存密码”后仅在本次运行中使用") from None
        else:
            # 不自动删除已有持久凭据；单独的“清除已保存密码”按钮控制删除。
            pass
        self.memory[credential_id] = password

    def delete(self, credential_id):
        try:
            backend = self.backend()
            if backend.get_password(SERVICE, credential_id) is not None:
                backend.delete_password(SERVICE, credential_id)
        except Exception:
            raise RuntimeError("无法清除系统密钥环中的密码，请先解锁密钥环") from None
        self.memory.pop(credential_id, None)
