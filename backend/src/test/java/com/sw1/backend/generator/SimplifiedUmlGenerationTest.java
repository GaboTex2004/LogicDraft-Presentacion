package com.sw1.backend.generator;

import com.sw1.backend.ai.image.ImageDiagramReconciler;
import com.sw1.backend.generator.export.FullStackGenerator;
import com.sw1.backend.generator.flutter.FlutterGenerator;
import com.sw1.backend.generator.mapper.ApplicationSchemaMapper;
import com.sw1.backend.generator.schema.ApplicationCardinality;
import com.sw1.backend.generator.schema.ApplicationSchema;
import com.sw1.backend.generator.spring.SpringBootGenerator;
import com.sw1.backend.generator.validation.ApplicationSchemaException;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.ValueSource;
import static org.junit.jupiter.api.Assertions.*;

class SimplifiedUmlGenerationTest {
    static Map<String, Object> document() {
        return new LinkedHashMap<>(Map.of("version", 2,
                "nodes", List.of(node("persona", "Persona"), node("alumno", "Alumno"),
                        node("curso", "Curso"), node("direccion", "Direccion")),
                "edges", List.of(
                        edge("hereda", "alumno", "persona", "GENERALIZATION", "ONE_ONE", "ONE_ONE"),
                        edge("agrega", "curso", "alumno", "AGGREGATION", "ONE_ONE", "ZERO_MANY"),
                        edge("compone", "persona", "direccion", "COMPOSITION", "ONE_ONE", "ZERO_MANY"))));
    }

    @Test
    void translatesUmlRelationsToForeignKeysWithoutChangingTheDocument() {
        var document = document();
        var before = ImageDiagramReconciler.copy(document);
        var schema = ApplicationSchemaMapper.fromDocument("Academia", document);
        assertEquals(3, schema.relationships().size());
        var inheritance = schema.relationships().stream().filter(r -> r.id().equals("hereda")).findFirst().orElseThrow();
        assertEquals("persona", inheritance.sourceEntityId());
        assertEquals("alumno", inheritance.targetEntityId());
        assertEquals(ApplicationCardinality.ONE_ONE, inheritance.sourceCardinality());
        assertEquals(ApplicationCardinality.ONE_ONE, inheritance.targetCardinality());

        var generated = new FullStackGenerator(new SpringBootGenerator(), new FlutterGenerator()).generate(schema);
        String javaRoot = "backend/src/main/java/com/logicdraft/generated/academia/";
        String child = generated.files().get(javaRoot + "entity/Alumno.java");
        assertTrue(child.contains("@OneToOne"));
        assertTrue(child.contains("name = \"persona_id\""));
        assertTrue(child.contains("name = \"curso_id\""));
        assertFalse(child.contains("extends Persona"));
        assertTrue(generated.files().get(javaRoot + "entity/Direccion.java").contains("name = \"persona_id\""));
        assertTrue(generated.files().get(javaRoot + "dto/AlumnoCreateRequest.java").contains("personaId"));
        assertTrue(generated.files().get("frontend/lib/models/alumno.dart").contains("personaId"));
        assertTrue(generated.files().get("frontend/lib/models/direccion.dart").contains("personaId"));
        assertEquals(before, document);
    }

    @ParameterizedTest
    @ValueSource(strings = {"AGGREGATION", "COMPOSITION"})
    void preservesTheSameCardinalitiesAsAnOrdinaryAssociation(String kind) {
        for (String source : List.of("ONE_ONE", "ZERO_MANY")) {
            for (String target : List.of("ZERO_ONE", "ONE_MANY")) {
                var ordinary = pair("ASSOCIATION", source, target);
                var specialized = pair(kind, source, target);
                assertEquals(ApplicationSchemaMapper.fromDocument("Academia", ordinary).relationships(),
                        ApplicationSchemaMapper.fromDocument("Academia", specialized).relationships());
            }
        }
    }

    @Test
    void requiresBothEndsToBeGenerableInsteadOfSilentlyDroppingTheForeignKey() {
        var document = pair("GENERALIZATION", "ONE_ONE", "ONE_ONE");
        var parent = (Map<String, Object>) ((Map<?, ?>) ((List<?>) document.get("nodes")).getFirst()).get("data");
        parent.remove("generateCrud");
        var error = assertThrows(ApplicationSchemaException.class,
                () -> ApplicationSchemaMapper.fromDocument("Academia", document));
        assertTrue(error.getMessage().contains("ambos extremos"));
    }

    private static Map<String, Object> pair(String kind, String source, String target) {
        return new LinkedHashMap<>(Map.of("version", 2,
                "nodes", List.of(node("persona", "Persona"), node("alumno", "Alumno")),
                "edges", List.of(edge("relacion", "alumno", "persona", kind, source, target))));
    }

    private static Map<String, Object> node(String id, String name) {
        return new LinkedHashMap<>(Map.of("id", id, "type", "entity", "position", Map.of("x", 0, "y", 0),
                "data", new LinkedHashMap<>(Map.of("id", id, "name", name, "kind", "CLASS", "generateCrud", true,
                        "attributes", List.of(Map.of("id", id + "-id", "name", "id", "type", "INTEGER",
                                "primaryKey", true, "nullable", false)), "methods", List.of()))));
    }

    private static Map<String, Object> edge(String id, String source, String target, String kind,
            String sourceCardinality, String targetCardinality) {
        return Map.of("id", id, "source", source, "target", target, "data", Map.of("relationKind", kind,
                "sourceCardinality", sourceCardinality, "targetCardinality", targetCardinality));
    }
}
