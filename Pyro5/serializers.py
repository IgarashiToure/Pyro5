"""
The various serializers.

Pyro - Python Remote Objects.  Copyright by Irmen de Jong (irmen@razorvine.net).
"""

import zlib
import uuid
import logging
import struct
import datetime
import decimal
import numbers
from . import errors

log = logging.getLogger("Pyro5.serializers")


all_exceptions = {}
import builtins
for name, t in vars(builtins).items():
    if type(t) is type and issubclass(t, BaseException):
        all_exceptions[name] = t
buffer = bytearray
for name, t in vars(errors).items():
    if type(t) is type and issubclass(t, errors.PyroError):
        all_exceptions[name] = t


class SerializerBase(object):
    """Base class for (de)serializer implementations (which must be thread safe)"""
    __custom_class_to_dict_registry = {}
    __custom_dict_to_class_registry = {}

    def serializeData(self, data, compress=False):
        """Serialize the given data object, try to compress if told so.
        Returns a tuple of the serialized data (bytes) and a bool indicating if it is compressed or not."""
        data = self.dumps(data)
        return self.__compressdata(data, compress)

    def deserializeData(self, data, compressed=False):
        """Deserializes the given data (bytes). Set compressed to True to decompress the data first."""
        if compressed:
            data = zlib.decompress(data)
        return self.loads(data)

    def serializeCall(self, obj, method, vargs, kwargs, compress=False):
        """Serialize the given method call parameters, try to compress if told so.
        Returns a tuple of the serialized data and a bool indicating if it is compressed or not."""
        data = self.dumpsCall(obj, method, vargs, kwargs)
        return self.__compressdata(data, compress)

    def deserializeCall(self, data, compressed=False):
        """Deserializes the given call data back to (object, method, vargs, kwargs) tuple.
        Set compressed to True to decompress the data first."""
        if compressed:
            data = zlib.decompress(data)
        return self.loadsCall(data)

    def loads(self, data):
        raise NotImplementedError("implement in subclass")

    def loadsCall(self, data):
        raise NotImplementedError("implement in subclass")

    def dumps(self, data):
        raise NotImplementedError("implement in subclass")

    def dumpsCall(self, obj, method, vargs, kwargs):
        raise NotImplementedError("implement in subclass")

    def _convertToBytes(self, data):
        t = type(data)
        if t is not bytes:
            if t in (bytearray, buffer):
                return bytes(data)
            if t is memoryview:
                return data.tobytes()
        return data

    def __compressdata(self, data, compress):
        if not compress or len(data) < 200:
            return data, False  # don't waste time compressing small messages
        compressed = zlib.compress(data)
        if len(compressed) < len(data):
            return compressed, True
        return data, False

    @classmethod
    def register_type_replacement(cls, object_type, replacement_function):
        raise NotImplementedError("implement in subclass")

    @classmethod
    def register_class_to_dict(cls, clazz, converter, serpent_too=True):
        """Registers a custom function that returns a dict representation of objects of the given class.
        The function is called with a single parameter; the object to be converted to a dict."""
        cls.__custom_class_to_dict_registry[clazz] = converter
        if serpent_too:
            try:
                get_serializer_by_id(SerpentSerializer.serializer_id)
                import serpent

                def serpent_converter(obj, serializer, stream, level):
                    d = converter(obj)
                    serializer.ser_builtins_dict(d, stream, level)

                serpent.register_class(clazz, serpent_converter)
            except errors.ProtocolError:
                pass

    @classmethod
    def unregister_class_to_dict(cls, clazz):
        """Removes the to-dict conversion function registered for the given class. Objects of the class
        will be serialized by the default mechanism again."""
        if clazz in cls.__custom_class_to_dict_registry:
            del cls.__custom_class_to_dict_registry[clazz]
        try:
            get_serializer_by_id(SerpentSerializer.serializer_id)
            import serpent
            serpent.unregister_class(clazz)
        except errors.ProtocolError:
            pass

    @classmethod
    def register_dict_to_class(cls, classname, converter):
        """
        Registers a custom converter function that creates objects from a dict with the given classname tag in it.
        The function is called with two parameters: the classname and the dictionary to convert to an instance of the class.
        """
        cls.__custom_dict_to_class_registry[classname] = converter

    @classmethod
    def unregister_dict_to_class(cls, classname):
        """
        Removes the converter registered for the given classname. Dicts with that classname tag
        will be deserialized by the default mechanism again.
        """
        if classname in cls.__custom_dict_to_class_registry:
            del cls.__custom_dict_to_class_registry[classname]

    @classmethod
    def class_to_dict(cls, obj):
        """
        Convert a non-serializable object to a dict. Partly borrowed from serpent.
        """
        for clazz in cls.__custom_class_to_dict_registry:
            if isinstance(obj, clazz):
                return cls.__custom_class_to_dict_registry[clazz](obj)
        if type(obj) in (set, dict, tuple, list):
            # we use a ValueError to mirror the exception type returned by serpent and other serializers
            raise ValueError("can't serialize type " + str(obj.__class__) + " into a dict")
        if hasattr(obj, "_pyroDaemon"):
            obj._pyroDaemon = None
        if isinstance(obj, BaseException):
            # special case for exceptions
            return {
                "__class__": obj.__class__.__module__ + "." + obj.__class__.__name__,
                "__exception__": True,
                "args": obj.args,
                "attributes": vars(obj)  # add custom exception attributes
            }
        try:
            value = obj.__getstate__()
        except AttributeError:
            pass
        else:
            if isinstance(value, dict):
                return value
        try:
            value = dict(vars(obj))  # make sure we can serialize anything that resembles a dict
            value["__class__"] = obj.__class__.__module__ + "." + obj.__class__.__name__
            return value
        except TypeError:
            if hasattr(obj, "__slots__"):
                # use the __slots__ instead of the vars dict
                value = {}
                for slot in obj.__slots__:
                    value[slot] = getattr(obj, slot)
                value["__class__"] = obj.__class__.__module__ + "." + obj.__class__.__name__
                return value
            else:
                raise errors.SerializeError("don't know how to serialize class " + str(obj.__class__) +
                                            " using serializer " + str(cls.__name__) +
                                            ". Give it vars() or an appropriate __getstate__")

    @classmethod
    def dict_to_class(cls, data):
        """
        Recreate an object out of a dict containing the class name and the attributes.
        Only a fixed set of classes are recognized.
        """
        from . import core   # XXX circular
        classname = data.get("__class__", "<unknown>")
        if isinstance(classname, bytes):
            classname = classname.decode("utf-8")
        if classname in cls.__custom_dict_to_class_registry:
            converter = cls.__custom_dict_to_class_registry[classname]
            return converter(classname, data)
        if "__" in classname:
            raise errors.SecurityError("refused to deserialize types with double underscores in their name: " + classname)
        # for performance, the constructors below are hardcoded here instead of added on a per-class basis to the dict-to-class registry
        if classname.startswith("Pyro5.core."):
            if classname == "Pyro5.core.URI":
                uri = core.URI.__new__(core.URI)
                uri.__setstate_from_dict__(data["state"])
                return uri
            elif classname == "Pyro5.core.Proxy":
                proxy = core.Proxy.__new__(core.Proxy)
                proxy.__setstate_from_dict__(data["state"])
                return proxy
            elif classname == "Pyro5.core.Daemon":
                daemon = core.Daemon.__new__(core.Daemon)
                daemon.__setstate_from_dict__(data["state"])
                return daemon
        elif classname.startswith("Pyro5.util."):
            if classname == "Pyro5.util.SerpentSerializer":
                return SerpentSerializer()
            elif classname == "Pyro5.util.MarshalSerializer":
                return MarshalSerializer()
            elif classname == "Pyro5.util.JsonSerializer":
                return JsonSerializer()
            elif classname == "Pyro5.util.MsgpackSerializer":
                return MsgpackSerializer()
        elif classname.startswith("Pyro5.errors."):
            errortype = getattr(errors, classname.split('.', 2)[2])
            if issubclass(errortype, errors.PyroError):
                return SerializerBase.make_exception(errortype, data)
        elif classname == "Pyro5.core._ExceptionWrapper":
            ex = data["exception"]
            if isinstance(ex, dict) and "__class__" in ex:
                ex = SerializerBase.dict_to_class(ex)
            return core._ExceptionWrapper(ex)
        elif data.get("__exception__", False):
            if classname in all_exceptions:
                return SerializerBase.make_exception(all_exceptions[classname], data)
            # python 2.x: exceptions.ValueError
            # python 3.x: builtins.ValueError
            # translate to the appropriate namespace...
            namespace, short_classname = classname.split('.', 1)
            if namespace in ("builtins", "exceptions"):
                exceptiontype = getattr(builtins, short_classname)
                if issubclass(exceptiontype, BaseException):
                    return SerializerBase.make_exception(exceptiontype, data)
            elif namespace == "sqlite3" and short_classname.endswith("Error"):
                import sqlite3
                exceptiontype = getattr(sqlite3, short_classname)
                if issubclass(exceptiontype, BaseException):
                    return SerializerBase.make_exception(exceptiontype, data)
        log.warning("unsupported serialized class: " + classname)
        raise errors.SerializeError("unsupported serialized class: " + classname)

    @staticmethod
    def make_exception(exceptiontype, data):
        ex = exceptiontype(*data["args"])
        if "attributes" in data:
            # restore custom attributes on the exception object
            for attr, value in data["attributes"].items():
                setattr(ex, attr, value)
        return ex

    def recreate_classes(self, literal):
        t = type(literal)
        if t is set:
            return {self.recreate_classes(x) for x in literal}
        if t is list:
            return [self.recreate_classes(x) for x in literal]
        if t is tuple:
            return tuple(self.recreate_classes(x) for x in literal)
        if t is dict:
            if "__class__" in literal:
                return self.dict_to_class(literal)
            result = {}
            for key, value in literal.items():
                result[key] = self.recreate_classes(value)
            return result
        return literal

    def __eq__(self, other):
        """this equality method is only to support the unit tests of this class"""
        return isinstance(other, SerializerBase) and vars(self) == vars(other)

    def __ne__(self, other):
        return not self.__eq__(other)

    __hash__ = object.__hash__


class MarshalSerializer(SerializerBase):
    """(de)serializer that wraps the marshal serialization protocol."""
    serializer_id = 3  # never change this

    def dumpsCall(self, obj, method, vargs, kwargs):
        return marshal.dumps((obj, method, vargs, kwargs))

    def dumps(self, data):
        try:
            return marshal.dumps(data)
        except (ValueError, TypeError):
            return marshal.dumps(self.class_to_dict(data))

    def loadsCall(self, data):
        data = self._convertToBytes(data)
        obj, method, vargs, kwargs = marshal.loads(data)
        vargs = self.recreate_classes(vargs)
        kwargs = self.recreate_classes(kwargs)
        return obj, method, vargs, kwargs

    def loads(self, data):
        data = self._convertToBytes(data)
        return self.recreate_classes(marshal.loads(data))

    @classmethod
    def class_to_dict(cls, obj):
        if isinstance(obj, uuid.UUID):
            return str(obj)
        return super(MarshalSerializer, cls).class_to_dict(obj)

    @classmethod
    def register_type_replacement(cls, object_type, replacement_function):
        pass  # marshal serializer doesn't support per-type hooks


class SerpentSerializer(SerializerBase):
    """(de)serializer that wraps the serpent serialization protocol."""
    serializer_id = 1  # never change this

    def dumpsCall(self, obj, method, vargs, kwargs):
        return serpent.dumps((obj, method, vargs, kwargs), module_in_classname=True)

    def dumps(self, data):
        return serpent.dumps(data, module_in_classname=True)

    def loadsCall(self, data):
        obj, method, vargs, kwargs = serpent.loads(data)
        vargs = self.recreate_classes(vargs)
        kwargs = self.recreate_classes(kwargs)
        return obj, method, vargs, kwargs

    def loads(self, data):
        return self.recreate_classes(serpent.loads(data))

    @classmethod
    def register_type_replacement(cls, object_type, replacement_function):
        def custom_serializer(object, serpent_serializer, outputstream, indentlevel):
            replaced = replacement_function(object)
            if replaced is object:
                serpent_serializer.ser_default_class(replaced, outputstream, indentlevel)
            else:
                serpent_serializer._serialize(replaced, outputstream, indentlevel)

        serpent.register_class(object_type, custom_serializer)

    @classmethod
    def dict_to_class(cls, data):
        if data.get("__class__") == "float":
            return float(data["value"])     # serpent encodes a float nan as a special class dict like this
        return super(SerpentSerializer, cls).dict_to_class(data)


class JsonSerializer(SerializerBase):
    """(de)serializer that wraps the json serialization protocol."""
    serializer_id = 2  # never change this

    __type_replacements = {}

    def dumpsCall(self, obj, method, vargs, kwargs):
        data = {"object": obj, "method": method, "params": vargs, "kwargs": kwargs}
        data = json.dumps(data, ensure_ascii=False, default=self.default)
        return data.encode("utf-8")

    def dumps(self, data):
        data = json.dumps(data, ensure_ascii=False, default=self.default)
        return data.encode("utf-8")

    def loadsCall(self, data):
        data = self._convertToBytes(data).decode("utf-8")
        data = json.loads(data)
        vargs = self.recreate_classes(data["params"])
        kwargs = self.recreate_classes(data["kwargs"])
        return data["object"], data["method"], vargs, kwargs

    def loads(self, data):
        data = self._convertToBytes(data).decode("utf-8")
        return self.recreate_classes(json.loads(data))

    def default(self, obj):
        replacer = self.__type_replacements.get(type(obj), None)
        if replacer:
            obj = replacer(obj)
        if isinstance(obj, set):
            return tuple(obj)  # json module can't deal with sets so we make a tuple out of it
        if isinstance(obj, uuid.UUID):
            return str(obj)
        if isinstance(obj, (datetime.datetime, datetime.date)):
            return obj.isoformat()
        if isinstance(obj, decimal.Decimal):
            return str(obj)
        return self.class_to_dict(obj)

    @classmethod
    def register_type_replacement(cls, object_type, replacement_function):
        cls.__type_replacements[object_type] = replacement_function


class MsgpackSerializer(SerializerBase):
    """(de)serializer that wraps the msgpack serialization protocol."""
    serializer_id = 6  # never change this

    __type_replacements = {}

    def dumpsCall(self, obj, method, vargs, kwargs):
        return msgpack.packb((obj, method, vargs, kwargs), use_bin_type=True, default=self.default)

    def dumps(self, data):
        return msgpack.packb(data, use_bin_type=True, default=self.default)

    def loadsCall(self, data):
        data = self._convertToBytes(data)
        obj, method, vargs, kwargs = msgpack.unpackb(data, encoding="utf-8", object_hook=self.object_hook)
        return obj, method, vargs, kwargs

    def loads(self, data):
        data = self._convertToBytes(data)
        return msgpack.unpackb(data, encoding="utf-8", object_hook=self.object_hook, ext_hook=self.ext_hook)

    def default(self, obj):
        replacer = self.__type_replacements.get(type(obj), None)
        if replacer:
            obj = replacer(obj)
        if isinstance(obj, set):
            return tuple(obj)  # msgpack module can't deal with sets so we make a tuple out of it
        if isinstance(obj, uuid.UUID):
            return str(obj)
        if isinstance(obj, bytearray):
            return bytes(obj)
        if isinstance(obj, complex):
            return msgpack.ExtType(0x30, struct.pack("dd", obj.real, obj.imag))
        if isinstance(obj, datetime.datetime):
            if obj.tzinfo:
                raise errors.SerializeError("msgpack cannot serialize datetime with timezone info")
            return msgpack.ExtType(0x32, struct.pack("d", obj.timestamp()))
        if isinstance(obj, datetime.date):
            return msgpack.ExtType(0x33, struct.pack("l", obj.toordinal()))
        if isinstance(obj, decimal.Decimal):
            return str(obj)
        if isinstance(obj, numbers.Number):
            return msgpack.ExtType(0x31, str(obj).encode("ascii"))     # long
        return self.class_to_dict(obj)

    def object_hook(self, obj):
        if "__class__" in obj:
            return self.dict_to_class(obj)
        return obj

    def ext_hook(self, code, data):
        if code == 0x30:
            real, imag = struct.unpack("dd", data)
            return complex(real, imag)
        if code == 0x31:
            return int(data)
        if code == 0x32:
            return datetime.datetime.fromtimestamp(struct.unpack("d", data)[0])
        if code == 0x33:
            return datetime.date.fromordinal(struct.unpack("l", data)[0])
        raise errors.SerializeError("invalid ext code for msgpack: " + str(code))

    @classmethod
    def register_type_replacement(cls, object_type, replacement_function):
        cls.__type_replacements[object_type] = replacement_function


"""The various serializers that are supported"""
_serializers = {}
_serializers_by_id = {}


def get_serializer(name):
    try:
        return _serializers[name]
    except KeyError:
        raise errors.SerializeError("serializer '%s' is unknown or not available" % name)


def get_serializer_by_id(sid):
    try:
        return _serializers_by_id[sid]
    except KeyError:
        raise errors.SerializeError("no serializer available for id %d" % sid)

# determine the serializers that are supported
import marshal
_ser = MarshalSerializer()
_serializers["marshal"] = _ser
_serializers_by_id[_ser.serializer_id] = _ser
try:
    import json
    _ser = JsonSerializer()
    _serializers["json"] = _ser
    _serializers_by_id[_ser.serializer_id] = _ser
except ImportError:
    pass
try:
    import serpent
    if '-' in serpent.__version__:
        ver = serpent.__version__.split('-', 1)[0]
    else:
        ver = serpent.__version__
    ver = tuple(map(int, ver.split(".")))
    if ver < (1, 23):  # serpent 1.23 required
        raise RuntimeError("requires serpent 1.23 or better")
    _ser = SerpentSerializer()
    _serializers["serpent"] = _ser
    _serializers_by_id[_ser.serializer_id] = _ser
except ImportError:
    log.warning("serpent serializer is not available")
    pass
try:
    import msgpack
    _ser = MsgpackSerializer()
    _serializers["msgpack"] = _ser
    _serializers_by_id[_ser.serializer_id] = _ser
except ImportError:
    pass
del _ser
